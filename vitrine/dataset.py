"""Training views: images held on CPU, cropped windows delivered to the GPU.

Two ideas carry this module.

**Images stay on the CPU at high resolution.** 222 views at 2560 px is far more
than 6 GB of VRAM would hold, but it fits comfortably in system RAM, and only
the crop being trained on ever needs to reach the GPU.

**Each step renders a random window, not a whole view.** Rasterisation cost
scales with rendered area, so a 768x768 window of a 2560 px image costs about
a eleventh of the full frame while still exposing full-resolution detail to
the optimiser. The crop is expressed purely through the intrinsics::

    cx' = cx - x0        cy' = cy - y0
    width = height = crop

which is exactly the projection of that sub-window — no approximation. This is
what makes fine detail (the newspaper text on the installation walls)
affordable on a laptop.

A held-out split is reserved up front so quality can be *measured* rather than
asserted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from . import undistort as undistort_module
from .colmap_io import Model, scene_scale

logger = logging.getLogger(__name__)


@dataclass
class View:
    """One training image with its pose and intrinsics at working resolution."""

    name: str
    #: [H, W, 3] **uint8** on the CPU, converted to float on the GPU per batch.
    #:
    #: The sources are 8-bit JPEG and HEIC, so a float32 cache stored four
    #: bytes per channel to hold one byte of real information — and the cache
    #: is what limits working resolution, since it holds every registered view
    #: at once. 736 views at 2304 px cost ~31 GB as float32, which did not fit
    #: in this machine's 32 GB and forced the capture to be trained at a lower
    #: resolution than it was shot. As uint8 the same cache is ~7.8 GB.
    #: Conversion is a per-crop operation on data already being moved to the
    #: GPU, so it costs nothing measurable.
    image: torch.Tensor
    #: [4, 4] world-to-camera.
    world_to_camera: torch.Tensor
    #: [3, 3] intrinsics matching ``image``'s resolution.
    intrinsics: torch.Tensor
    camera_id: int

    @property
    def height(self) -> int:
        return self.image.shape[0]

    @property
    def width(self) -> int:
        return self.image.shape[1]


@dataclass
class Batch:
    """A single cropped view, on the GPU, ready for the rasteriser."""

    image: torch.Tensor          # [H, W, 3]
    world_to_camera: torch.Tensor  # [1, 4, 4]
    intrinsics: torch.Tensor       # [1, 3, 3]
    width: int
    height: int
    view_index: int


class ViewSet:
    """All registered views, plus the crop sampler and the eval split."""

    def __init__(
        self,
        model: Model,
        images_dir: Path,
        *,
        long_edge: int,
        holdout_every: int = 8,
        device: str = "cuda",
        undistort: bool = True,
    ) -> None:
        self.device = device
        self.scene_scale = scene_scale(model)
        self.views: list[View] = []

        images_dir = Path(images_dir)
        missing: list[str] = []
        described: set[int] = set()

        for image_meta in sorted(model.images, key=lambda im: im.name):
            path = self._locate(images_dir, image_meta.name)
            if path is None:
                missing.append(image_meta.name)
                continue

            camera = model.camera_for(image_meta)
            with Image.open(path) as handle:
                pil = handle.convert("RGB")
                # COLMAP's stored size is authoritative for the intrinsics; if
                # the file on disk differs, scale K to the file rather than
                # resampling the image to match the model.
                native_w, native_h = pil.size
                scale = min(1.0, long_edge / max(native_w, native_h))
                target_w = max(32, round(native_w * scale))
                target_h = max(32, round(native_h * scale))
                if (target_w, target_h) != (native_w, native_h):
                    pil = pil.resize((target_w, target_h), Image.Resampling.LANCZOS)
                # np.array, not asarray: asarray hands back a read-only view of
                # PIL's buffer, and torch.from_numpy on that yields a tensor
                # torch considers unsafe to write.
                array = np.array(pil, dtype=np.uint8)

            k = torch.from_numpy(camera.scaled_intrinsics(target_w, target_h))
            tensor = torch.from_numpy(array)

            # The rasteriser is a pinhole projector; COLMAP solved a distorted
            # camera. Reconcile them here, once, rather than asking the
            # optimiser to absorb the difference as blur. Resampling needs
            # floats, but the result goes back to uint8 — the source was 8-bit
            # to begin with, so nothing is lost that was ever there.
            if undistort and camera.has_distortion:
                if camera.id not in described:
                    logger.info("undistorting %s", undistort_module.describe(camera, target_w, target_h, k))
                    described.add(camera.id)
                corrected, k = undistort_module.undistort(tensor.float(), k, camera)
                tensor = corrected.round_().clamp_(0, 255).to(torch.uint8)

            self.views.append(
                View(
                    name=image_meta.name,
                    image=tensor,
                    world_to_camera=torch.from_numpy(image_meta.world_to_camera()),
                    intrinsics=k,
                    camera_id=image_meta.camera_id,
                )
            )

        if missing:
            logger.warning(
                "%d registered images not found on disk (first few: %s)",
                len(missing), ", ".join(missing[:5]),
            )
        if not self.views:
            raise RuntimeError(f"no training images could be loaded from {images_dir}")

        # Deterministic held-out split: every Nth view by sorted name. Regular
        # spacing keeps the eval set spread across the capture path rather
        # than clustered in whichever corner was photographed last.
        self.eval_indices = list(range(0, len(self.views), holdout_every)) if holdout_every > 0 else []
        eval_set = set(self.eval_indices)
        self.train_indices = [i for i in range(len(self.views)) if i not in eval_set]

        self.sample_weights = self._sampling_weights()

        resolutions = {(v.width, v.height) for v in self.views}
        logger.info(
            "%d views loaded (%d train / %d eval), %d resolution(s), scene_scale=%.3f",
            len(self.views), len(self.train_indices), len(self.eval_indices),
            len(resolutions), self.scene_scale,
        )

    def _sampling_weights(self) -> torch.Tensor:
        """Per-training-view sampling probability, favouring the sharp master.

        Uniform sampling is wrong when a capture mixes sources of different
        quality, and the failure is subtle: it looks like a converged model
        that is simply soft.

        On the *Nested Cinema* capture, 150 motion-blurred 720p video frames
        outnumbered 72 sharp 25 MP stills two to one, so two thirds of every
        gradient step pulled the model toward reproducing blur. Measured
        against held-out views, the video reconstructed at 20.9 dB / 0.875 SSIM
        while the stills — the archival master, the images that actually
        contain the readable newspaper text — managed only 16.1 dB / 0.552.
        The optimiser was doing exactly what it was asked; it was asked for the
        wrong thing.

        Weighting by pixel count restores the balance. A 1536x2048 still
        carries about 3.4x the pixels of a 1280x720 frame and far more
        information per pixel, so it earns proportionally more of the budget
        while video still contributes the coverage it was captured for.
        """
        if not self.train_indices:
            return torch.zeros(0)

        pixels = torch.tensor(
            [float(self.views[i].width * self.views[i].height) for i in self.train_indices],
            dtype=torch.float64,
        )
        weights = pixels / pixels.sum()

        by_group: dict[tuple[int, int], float] = {}
        for index, weight in zip(self.train_indices, weights.tolist()):
            key = (self.views[index].width, self.views[index].height)
            by_group[key] = by_group.get(key, 0.0) + weight
        if len(by_group) > 1:
            shares = ", ".join(
                f"{w * 100:.0f}% at {k[0]}x{k[1]}" for k, w in sorted(by_group.items(), key=lambda kv: -kv[1])
            )
            logger.info("view sampling weighted by resolution: %s", shares)

        return weights.float()

    def sample_train_index(self, generator: torch.Generator | None = None) -> int:
        """Draw a training view, weighted toward higher-resolution sources."""
        position = int(torch.multinomial(self.sample_weights, 1, generator=generator).item())
        return self.train_indices[position]

    def stratified_eval_indices(self, limit: int) -> list[int]:
        """A subset of the eval split covering every camera group.

        Intermediate evaluations sample only a few views for speed. Taking the
        first N is wrong here: COLMAP names sort by folder, so the first N are
        all from one group and the reported number silently describes a single
        camera rather than the capture.
        """
        if limit <= 0 or limit >= len(self.eval_indices):
            return list(self.eval_indices)

        groups: dict[tuple[int, int], list[int]] = {}
        for index in self.eval_indices:
            key = (self.views[index].width, self.views[index].height)
            groups.setdefault(key, []).append(index)

        chosen: list[int] = []
        position = 0
        while len(chosen) < limit:
            added = False
            for members in groups.values():
                if position < len(members) and len(chosen) < limit:
                    chosen.append(members[position])
                    added = True
            if not added:
                break
            position += 1
        return sorted(chosen)

    @staticmethod
    def _locate(root: Path, name: str) -> Path | None:
        """Find an image by COLMAP's recorded name.

        COLMAP records paths relative to its ``image_path``, so a multi-camera
        run stores names like ``stills/IMG_6319.jpg``. Fall back to a basename
        search for models produced by other tools.
        """
        direct = root / name
        if direct.is_file():
            return direct
        matches = list(root.rglob(Path(name).name))
        return matches[0] if matches else None

    def __len__(self) -> int:
        return len(self.views)

    def crop(self, index: int, crop: int, generator: torch.Generator | None = None) -> Batch:
        """Random ``crop`` x ``crop`` window from view ``index``, on the device.

        Views smaller than the crop are used whole. The principal point is
        shifted by the crop origin, which is what makes the render an exact
        sub-window rather than a rescale.
        """
        view = self.views[index]
        height, width = view.height, view.width
        size_h = min(crop, height)
        size_w = min(crop, width)

        if height > size_h:
            y0 = int(torch.randint(0, height - size_h + 1, (1,), generator=generator).item())
        else:
            y0 = 0
        if width > size_w:
            x0 = int(torch.randint(0, width - size_w + 1, (1,), generator=generator).item())
        else:
            x0 = 0

        patch = view.image[y0 : y0 + size_h, x0 : x0 + size_w, :]

        k = view.intrinsics.clone()
        k[0, 2] -= x0
        k[1, 2] -= y0

        return Batch(
            image=patch.to(self.device, non_blocking=True).float().div_(255.0),
            world_to_camera=view.world_to_camera.unsqueeze(0).to(self.device),
            intrinsics=k.unsqueeze(0).to(self.device),
            width=size_w,
            height=size_h,
            view_index=index,
        )

    def full(self, index: int, max_long_edge: int | None = None) -> Batch:
        """A whole view, optionally downscaled — used for eval and previews.

        Evaluating on crops would make the metric depend on which windows were
        sampled, so evaluation always uses complete frames.
        """
        view = self.views[index]
        image = view.image.to(self.device, non_blocking=True).float().div_(255.0)
        k = view.intrinsics.clone()
        height, width = view.height, view.width

        if max_long_edge is not None and max(width, height) > max_long_edge:
            scale = max_long_edge / max(width, height)
            new_w, new_h = max(32, round(width * scale)), max(32, round(height * scale))
            image = torch.nn.functional.interpolate(
                image.permute(2, 0, 1).unsqueeze(0),
                size=(new_h, new_w), mode="bilinear", align_corners=False, antialias=True,
            ).squeeze(0).permute(1, 2, 0)
            k[0, :] *= new_w / width
            k[1, :] *= new_h / height
            width, height = new_w, new_h

        return Batch(
            image=image,
            world_to_camera=view.world_to_camera.unsqueeze(0).to(self.device),
            intrinsics=k.unsqueeze(0).to(self.device),
            width=width,
            height=height,
            view_index=index,
        )

    def memory_footprint_gb(self) -> float:
        return sum(v.image.numel() * v.image.element_size() for v in self.views) / 2**30
