"""Derivative outputs: web-viewable splats and flat preview renders.

Everything here is regenerable from ``model/scene.ply``, which is what makes it
safe to re-encode as formats come and go. The master keeps full spherical
harmonics; these copies trade fidelity for portability.

Two derivatives, with quite different jobs:

**`.splat`** — the compact binary layout used by most browser viewers. 32 bytes
per Gaussian: position, scale, an 8-bit colour and an 8-bit quaternion. Band-0
spherical harmonics only, so view-dependent effects are lost and the result
looks flatter than the master. Roughly a tenth the size, and it loads in a web
page without a decoder.

**Preview renders** — ordinary PNG images from held-out viewpoints. Easy to
undervalue, but they are the only part of the output that needs no 3D software
at all. If every splat renderer has vanished in twenty years, these still open,
and they show what the model claimed to look like at the time it was made.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .ply import SH_C0, read_splat_ply

logger = logging.getLogger(__name__)


#: Cull Gaussians further from the scene centre than this multiple of the
#: median Gaussian's distance from it.
#:
#: Training leaves a tail of splats far outside the captured volume — COLMAP
#: outliers that were never pruned, and MCMC relocations that flew off. On
#: nested-cinema-04-master they reach **197,757** world units from the centre
#: of a capture whose cameras span 8.4, and 4% of live Gaussians sit beyond
#: 92. They are not merely wasteful: most web viewers frame a scene from its
#: bounding box, so one splat that far out shrinks the actual room to a dot.
#:
#: Measured against 24 held-out views, removing them costs nothing at all —
#: culling 10.5% of live Gaussians moved PSNR from 22.65 to 22.66 and left
#: SSIM identical to four decimals. They contribute to no view anyone captured.
#: The multiple is deliberately loose so that genuine distant geometry (through
#: a doorway, out of a window) survives; the tail this removes is orders of
#: magnitude beyond it.
#:
#: The master PLY keeps everything. This applies only to the web derivative,
#: which is the copy that has to load and frame itself in a browser.
SPLAT_RADIUS_MULTIPLE = 12.0


def write_splat_file(
    ply_path: Path,
    out_path: Path,
    *,
    opacity_floor: float = 1 / 255,
    radius_multiple: float | None = SPLAT_RADIUS_MULTIPLE,
) -> Path:
    """Convert a 3DGS PLY to the compact ``.splat`` layout.

    Per Gaussian, little-endian:

    ===========  ======  ===============================================
    offset       type    contents
    ===========  ======  ===============================================
    0            3f32    position xyz
    12           3f32    scale xyz, **linear** (the PLY stores log)
    24           4u8     colour rgba, sRGB-ish 0-255 (opacity in alpha)
    28           4u8     rotation wxyz, mapped from [-1,1] to [0,255]
    ===========  ======  ===============================================

    Splats below ``opacity_floor`` are dropped: they cannot be represented in
    8-bit alpha anyway, and carrying them wastes space and fill rate. Output is
    sorted by descending opacity, which lets a viewer show something coherent
    while the rest is still streaming.
    """
    data = read_splat_ply(Path(ply_path))
    count = int(data["count"])

    means = np.asarray(data["means"], dtype=np.float32)
    scales = np.exp(np.asarray(data["scales"], dtype=np.float32))
    quats = np.asarray(data["quats"], dtype=np.float32)
    opacities = 1.0 / (1.0 + np.exp(-np.asarray(data["opacities"], dtype=np.float32)))
    colours = np.asarray(data["sh0"], dtype=np.float32)[:, 0, :] * SH_C0 + 0.5

    keep = opacities >= opacity_floor
    dropped = int(count - keep.sum())

    culled = 0
    if radius_multiple is not None and keep.any():
        # Robust centre and extent from the *live* population: a mean or a
        # bounding box would be defined by the very outliers being removed.
        live_means = means[keep]
        centre = np.median(live_means, axis=0)
        distance = np.linalg.norm(means - centre, axis=1)
        median_distance = float(np.median(distance[keep]))
        if median_distance > 0:
            near = distance <= radius_multiple * median_distance
            culled = int((keep & ~near).sum())
            keep = keep & near

    means, scales, quats = means[keep], scales[keep], quats[keep]
    opacities, colours = opacities[keep], colours[keep]

    order = np.argsort(-opacities)
    means, scales, quats = means[order], scales[order], quats[order]
    opacities, colours = opacities[order], colours[order]

    kept = len(means)
    buffer = np.zeros((kept, 32), dtype=np.uint8)
    buffer[:, 0:12] = means.view(np.uint8).reshape(kept, 12)
    buffer[:, 12:24] = np.ascontiguousarray(scales).view(np.uint8).reshape(kept, 12)

    rgba = np.empty((kept, 4), dtype=np.uint8)
    rgba[:, :3] = np.clip(colours * 255.0, 0, 255).astype(np.uint8)
    rgba[:, 3] = np.clip(opacities * 255.0, 0, 255).astype(np.uint8)
    buffer[:, 24:28] = rgba

    norms = np.linalg.norm(quats, axis=1, keepdims=True)
    unit = quats / np.clip(norms, 1e-8, None)
    buffer[:, 28:32] = np.clip(unit * 128.0 + 128.0, 0, 255).astype(np.uint8)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(buffer.tobytes())

    source_mb = Path(ply_path).stat().st_size / 1e6
    out_mb = out_path.stat().st_size / 1e6
    logger.info(
        "wrote %s — %d Gaussians (%d below opacity floor, %d beyond %.0fx the median radius), "
        "%.1f MB from %.1f MB (%.0f%% smaller)",
        out_path.name, kept, dropped, culled, radius_multiple or 0,
        out_mb, source_mb, (1 - out_mb / max(source_mb, 1e-9)) * 100,
    )
    return out_path


def render_previews(
    ply_path: Path,
    out_dir: Path,
    views,
    *,
    indices: list[int] | None = None,
    max_long_edge: int = 1600,
    sh_degree: int = 3,
) -> list[Path]:
    """Render the trained model from selected viewpoints to PNG.

    Defaults to the held-out views, so the previews show the model predicting
    photographs it was never trained on — which is the honest thing to put in
    front of someone assessing quality.
    """
    import torch
    from PIL import Image as PILImage

    from . import cuda_toolkit

    cuda_toolkit.configure()
    from gsplat import rasterization

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data = read_splat_ply(Path(ply_path))
    device = views.device

    def tensor(name: str) -> torch.Tensor:
        return torch.from_numpy(np.ascontiguousarray(data[name])).float().to(device)

    means = tensor("means")
    scales = torch.exp(tensor("scales"))
    quats = torch.nn.functional.normalize(tensor("quats"), dim=-1)
    opacities = torch.sigmoid(tensor("opacities"))
    sh = torch.cat([tensor("sh0"), tensor("shN")], dim=1)
    degree = min(sh_degree, int(data["sh_degree"]))

    chosen = indices if indices is not None else views.eval_indices
    written: list[Path] = []

    with torch.no_grad():
        for index in chosen:
            batch = views.full(index, max_long_edge=max_long_edge)
            rendered, _, _ = rasterization(
                means=means, quats=quats, scales=scales, opacities=opacities,
                colors=sh, viewmats=batch.world_to_camera, Ks=batch.intrinsics,
                width=batch.width, height=batch.height, sh_degree=degree,
                packed=True, rasterize_mode="antialiased",
            )
            image = (rendered[0].clamp(0, 1) * 255).byte().cpu().numpy()
            name = Path(views.views[index].name).stem
            path = out_dir / f"{name}_render.png"
            PILImage.fromarray(image).save(path)

            # The source frame beside it, so the pair can be compared directly
            # without needing the original archive to hand.
            truth = (batch.image.clamp(0, 1) * 255).byte().cpu().numpy()
            truth_path = out_dir / f"{name}_source.png"
            PILImage.fromarray(truth).save(truth_path)
            written.extend([path, truth_path])

    logger.info("wrote %d preview images to %s", len(written), out_dir)
    return written
