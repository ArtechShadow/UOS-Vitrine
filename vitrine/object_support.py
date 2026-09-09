"""Contracts for evidence-backed object isolation and surface reconstruction.

The object sidecar is deliberately kept outside the core package.  This module
contains the small, format-neutral contract that crosses that process boundary:
which registered image observed an object, which calibrated camera produced it,
which exact detection/instance and mask were used, and how the mask is mapped
onto the image consumed by the renderer.

A sidecar may provide a perfectly valid Gaussian subset while omitting this
lineage.  That subset remains useful as an observed candidate, but it is not
sufficient evidence for an object *surface* reconstruction.  Callers should
therefore let :class:`ObjectSupportError` stop the mesh path and retain the
candidate and its diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import math
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .objects import resolve_contained, sha256_file


class ObjectSupportError(ValueError):
    """The selected object does not have sufficient trustworthy support."""


class MissingSupportEvidence(ObjectSupportError):
    """A candidate has no complete per-view mask/provenance evidence."""


@dataclass(frozen=True)
class SupportObservation:
    """One exact sidecar observation of an object.

    ``image_id`` is the COLMAP image ID, never a list position.  ``image_name``
    and ``camera_id`` are retained as cross-checks because an ID from another
    reconstruction can otherwise accidentally bind to a different frame.
    ``mask_path`` is relative to the core objects directory after import.
    """

    image_id: int
    image_name: str
    camera_id: int
    mask_path: str
    mask_sha256: str
    instance_id: str | int | None = None
    detection_id: str | int | None = None
    coordinate_space: str = "view"
    image_width: int | None = None
    image_height: int | None = None
    mask_width: int | None = None
    mask_height: int | None = None
    source_frame_id: int | str | None = None
    source_mask_path: str | None = None
    transform: Mapping[str, Any] | None = None

    @property
    def association_id(self) -> str:
        """Stable readable identity for diagnostics, preserving sidecar IDs."""
        value = self.instance_id if self.instance_id is not None else self.detection_id
        return f"{self.image_id}:{value}"

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "image_id": self.image_id,
            "image_name": self.image_name,
            "camera_id": self.camera_id,
            "mask_path": self.mask_path,
            "mask_sha256": self.mask_sha256,
            "coordinate_space": self.coordinate_space,
        }
        for name in ("instance_id", "detection_id", "image_width", "image_height",
                     "mask_width", "mask_height", "source_frame_id", "source_mask_path",
                     "transform"):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass
class ObjectSupport:
    """Selected, calibrated evidence bound to a :class:`dataset.ViewSet`.

    The object mesh path mutates ``depth_maps`` while fusing.  Keeping the
    rendered observations here makes the final mesh support check use exactly
    the same masks, cameras and depth convention as the point-cloud stage.
    """

    object_id: str
    observations: list[SupportObservation]
    view_indices: list[int]
    masks: dict[int, np.ndarray]
    scene_scale: float
    min_consistent_views: int = 2
    depth_convention: str = "camera_z"
    depth_maps: dict[int, np.ndarray] = field(default_factory=dict)
    scene_depth_maps: dict[int, np.ndarray] = field(default_factory=dict)
    view_shapes: dict[int, tuple[int, int]] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    observed_bounds: tuple[np.ndarray, np.ndarray] | None = None

    def mask_for(self, view_index: int, *, width: int | None = None, height: int | None = None) -> np.ndarray:
        """Return the nearest-resampled boolean mask for one ViewSet index."""
        try:
            mask = self.masks[view_index]
        except KeyError as exc:
            raise MissingSupportEvidence(
                f"object {self.object_id!r} has no mask bound to view index {view_index}; "
                "image IDs must be resolved explicitly"
            ) from exc
        if width is None or height is None or mask.shape == (height, width):
            return mask
        return resize_mask(mask, width, height)

    def observation_for_index(self, view_index: int) -> SupportObservation:
        for observation, bound_index in zip(self.observations, self.view_indices):
            if bound_index == view_index:
                return observation
        raise MissingSupportEvidence(f"No provenance observation bound to view index {view_index}")


def _finite_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise MissingSupportEvidence(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip() and value.strip().lstrip("+-").isdigit():
        return int(value)
    raise MissingSupportEvidence(f"{field} must be an integer")


def _id_value(value: Any, field: str) -> str | int:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value):
        raise MissingSupportEvidence(f"{field} must be a non-empty string or integer")
    return value


def _mask_reference(raw: Mapping[str, Any], field: str) -> tuple[str, str | None]:
    value = raw.get("mask_path")
    if value is None:
        value = raw.get("mask")
    if isinstance(value, Mapping):
        declared = value.get("sha256") or value.get("checksum")
        value = value.get("path") or value.get("mask_path")
    else:
        declared = raw.get("mask_sha256") or raw.get("mask_checksum") or raw.get("mask_hash")
    if not isinstance(value, str) or not value.strip():
        raise MissingSupportEvidence(f"{field}.mask_path is required; a source thumbnail is not a mask")
    return value, declared if isinstance(declared, str) and declared else None


def _candidate_evidence(record: Mapping[str, Any]) -> Any:
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        return None
    # ``evidence`` is the canonical importer contract.  The other names are
    # accepted only when a sidecar already supplied an explicit list; no frame
    # IDs or masks are inferred from a count or an array position.
    for key in ("evidence", "observations", "supporting_views", "views"):
        value = provenance.get(key)
        if isinstance(value, list):
            return value
    return None


def parse_support_observations(record: Mapping[str, Any]) -> list[SupportObservation]:
    """Parse canonical per-view evidence from one validated object record.

    This function intentionally raises for partial records.  Returning a
    partially useful list would allow a caller to accidentally substitute a
    thumbnail, basename or list index and publish unsupported geometry.
    """
    object_id = str(record.get("object_id", "<unknown>"))
    provenance = record.get("provenance")
    if isinstance(provenance, Mapping):
        support_status = provenance.get("support_status")
        if support_status is not None and support_status != "supported-evidence":
            issues = provenance.get("support_issues")
            detail = f"; issues={issues!r}" if issues else ""
            raise MissingSupportEvidence(
                f"object {object_id!r} is marked {support_status!r}; incomplete evidence "
                f"cannot be used for supported surface reconstruction{detail}"
            )
    raw_list = _candidate_evidence(record)
    if raw_list is None or not raw_list:
        raise MissingSupportEvidence(
            f"object {object_id!r} has no per-view mask evidence; "
            "object Gaussian candidates cannot satisfy surface reconstruction"
        )
    observations: list[SupportObservation] = []
    seen: dict[int, tuple[str, str, str | int | None, str | int | None]] = {}
    for index, raw in enumerate(raw_list):
        field = f"provenance.evidence[{index}]"
        if not isinstance(raw, Mapping):
            raise MissingSupportEvidence(f"{field} must be an object")
        image_raw = raw.get("image_id", raw.get("colmap_image_id"))
        frame_raw = raw.get("frame_id")
        if image_raw is None:
            raise MissingSupportEvidence(
                f"{field}.image_id is required; frame_id alone is not a COLMAP image identity"
            )
        image_id = _finite_int(image_raw, f"{field}.image_id")
        image_name = raw.get("image_name") or raw.get("image") or raw.get("name")
        if isinstance(image_name, Mapping):
            image_name = image_name.get("name") or image_name.get("path")
        if not isinstance(image_name, str) or not image_name.strip():
            raise MissingSupportEvidence(f"{field}.image_name is required")
        camera_id = _finite_int(raw.get("camera_id"), f"{field}.camera_id")
        mask_path, declared_hash = _mask_reference(raw, field)
        instance = raw.get("instance_id")
        detection = raw.get("detection_id")
        if instance is None:
            instance = raw.get("instance")
        if detection is None:
            detection = raw.get("detection")
        if instance is None and detection is None:
            raise MissingSupportEvidence(
                f"{field} needs instance_id or detection_id; same-frame labels are ambiguous"
            )
        instance = _id_value(instance, f"{field}.instance_id") if instance is not None else None
        detection = _id_value(detection, f"{field}.detection_id") if detection is not None else None
        coordinate_space = raw.get("coordinate_space", "view")
        if "coordinate_space" not in raw:
            raise MissingSupportEvidence(
                f"{field}.coordinate_space is required; mask orientation/rectification cannot be inferred"
            )
        if coordinate_space not in ("view", "rectified"):
            raise MissingSupportEvidence(
                f"{field}.coordinate_space must be 'view' or 'rectified', got {coordinate_space!r}"
            )
        dimensions: dict[str, int | None] = {}
        for name in ("image_width", "image_height", "mask_width", "mask_height"):
            value = raw.get(name)
            dimensions[name] = None if value is None else _finite_int(value, f"{field}.{name}")
            if dimensions[name] is not None and dimensions[name] <= 0:
                raise MissingSupportEvidence(f"{field}.{name} must be positive")
        source_frame = raw.get("source_frame_id", frame_raw)
        if source_frame is not None and isinstance(source_frame, (dict, list, bool)):
            raise MissingSupportEvidence(f"{field}.source_frame_id has invalid type")
        transform = raw.get("transform")
        if transform is not None and not isinstance(transform, Mapping):
            raise MissingSupportEvidence(f"{field}.transform must be an object when supplied")
        if coordinate_space == "rectified" and not isinstance(transform, Mapping):
            raise MissingSupportEvidence(
                f"{field}.transform is required for rectified masks so crop/intrinsics cannot be guessed"
            )
        if coordinate_space == "rectified":
            output_size = transform.get("output_size")
            if output_size is None and "width" in transform and "height" in transform:
                output_size = [transform.get("width"), transform.get("height")]
            if not (isinstance(output_size, (list, tuple)) and len(output_size) == 2):
                raise MissingSupportEvidence(
                    f"{field}.transform.output_size is required for rectified masks"
                )
            try:
                if int(output_size[0]) <= 0 or int(output_size[1]) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise MissingSupportEvidence(
                    f"{field}.transform.output_size must contain positive dimensions"
                ) from None
            rectified_k = transform.get("intrinsics", transform.get("K"))
            if not (
                isinstance(rectified_k, (list, tuple)) and len(rectified_k) == 3
                and all(isinstance(row, (list, tuple)) and len(row) == 3 for row in rectified_k)
            ):
                raise MissingSupportEvidence(
                    f"{field}.transform.intrinsics must be a 3x3 matrix for rectified masks"
                )
            try:
                if not np.isfinite(np.asarray(rectified_k, dtype=np.float64)).all():
                    raise ValueError
            except (TypeError, ValueError):
                raise MissingSupportEvidence(
                    f"{field}.transform.intrinsics must contain finite numbers"
                ) from None
        # The importer fills this hash after copying the mask.  A missing
        # declared hash is allowed here only so the importer can report that it
        # computed one; object_mesh always verifies the resulting hash.
        if not isinstance(declared_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", declared_hash):
            raise MissingSupportEvidence(
                f"{field}.mask_sha256 must be an explicit 64-character checksum"
            )
        mask_hash = declared_hash.lower()
        observation = SupportObservation(
            image_id=image_id, image_name=image_name, camera_id=camera_id,
            mask_path=mask_path, mask_sha256=mask_hash,
            instance_id=instance, detection_id=detection,
            coordinate_space=coordinate_space,
            image_width=dimensions["image_width"], image_height=dimensions["image_height"],
            mask_width=dimensions["mask_width"], mask_height=dimensions["mask_height"],
            source_frame_id=source_frame, source_mask_path=raw.get("source_mask_path"),
            transform=transform,
        )
        identity = (
            observation.mask_path, observation.mask_sha256,
            observation.instance_id, observation.detection_id,
        )
        prior = seen.get(image_id)
        if prior is not None and prior != identity:
            raise MissingSupportEvidence(
                f"{field} conflicts with another mask for image_id {image_id}; "
                "same-frame instance identity must be explicit"
            )
        seen[image_id] = identity
        observations.append(observation)
    return observations


def _camera_angle(a: np.ndarray, b: np.ndarray) -> float:
    a_norm = np.linalg.norm(a)
    b_norm = np.linalg.norm(b)
    if a_norm <= 1e-9 or b_norm <= 1e-9:
        return 0.0
    cosine = float(np.dot(a, b) / (a_norm * b_norm))
    return math.degrees(math.acos(float(np.clip(cosine, -1.0, 1.0))))


def select_supporting_observations(
    model: Any,
    observations: Sequence[SupportObservation],
    *,
    max_views: int = 120,
    min_views: int = 3,
    min_angle_degrees: float = 10.0,
    object_centroid: np.ndarray | None = None,
) -> tuple[list[SupportObservation], list[int]]:
    """Bind sidecar image IDs to model/list positions with diversity checks."""
    if max_views < 1 or min_views < 1:
        raise ValueError("max_views and min_views must be positive")
    by_id = {int(image.id): (index, image) for index, image in enumerate(model.images)}
    if len(by_id) != len(model.images):
        raise ObjectSupportError("COLMAP model contains duplicate image IDs")
    chosen: list[tuple[SupportObservation, int, Any]] = []
    seen_ids: set[int] = set()
    for observation in observations:
        entry = by_id.get(observation.image_id)
        if entry is None:
            raise MissingSupportEvidence(
                f"image_id {observation.image_id} from sidecar is absent from this COLMAP model"
            )
        index, image = entry
        if image.camera_id != observation.camera_id:
            raise MissingSupportEvidence(
                f"image_id {observation.image_id} camera mismatch: sidecar camera "
                f"{observation.camera_id}, COLMAP camera {image.camera_id}"
            )
        if image.name != observation.image_name:
            raise MissingSupportEvidence(
                f"image_id {observation.image_id} name mismatch: sidecar {observation.image_name!r}, "
                f"COLMAP {image.name!r}"
            )
        if observation.image_id in seen_ids:
            # Duplicate exact records are harmless; contradictory records were
            # rejected in parse_support_observations.
            continue
        seen_ids.add(observation.image_id)
        chosen.append((observation, index, image))
    if len(chosen) < min_views:
        raise MissingSupportEvidence(
            f"only {len(chosen)} calibrated object observations are available; at least {min_views} are required"
        )

    centre = np.asarray(object_centroid, dtype=np.float64) if object_centroid is not None else None
    if centre is None or centre.shape != (3,) or not np.isfinite(centre).all():
        points = np.stack([np.asarray(item[2].camera_centre(), dtype=np.float64) for item in chosen])
        centre = np.median(points, axis=0)
    directions = [np.asarray(item[2].camera_centre(), dtype=np.float64) - centre for item in chosen]

    # Greedy farthest-angle selection keeps front/rear/side views when the
    # sidecar provides more observations than the render budget.
    selected_positions = [0]
    while len(selected_positions) < min(max_views, len(chosen)):
        best_position = None
        best_score = -1.0
        for position in range(len(chosen)):
            if position in selected_positions:
                continue
            score = min(_camera_angle(directions[position], directions[current]) for current in selected_positions)
            if score > best_score:
                best_score, best_position = score, position
        if best_position is None:
            break
        selected_positions.append(best_position)
    selected = [chosen[position] for position in selected_positions]
    if len(selected) < min_views:
        raise MissingSupportEvidence("could not select the minimum number of distinct object views")
    angles = [
        _camera_angle(directions[a], directions[b])
        for a in selected_positions for b in selected_positions
        if a < b
    ]
    distinct_angles = max(angles, default=0.0)
    if distinct_angles < min_angle_degrees:
        raise MissingSupportEvidence(
            f"object observations lack angular diversity (maximum baseline angle {distinct_angles:.1f}°; "
            f"need at least {min_angle_degrees:.1f}°)"
        )
    return [item[0] for item in selected], [item[1] for item in selected]


def resize_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    """Nearest-neighbour resize for binary masks without smoothing boundaries."""
    mask = np.asarray(mask, dtype=bool)
    if mask.shape == (height, width):
        return mask
    if width <= 0 or height <= 0:
        raise ValueError("mask target dimensions must be positive")
    ys = np.minimum((np.arange(height) * mask.shape[0] / height).astype(int), mask.shape[0] - 1)
    xs = np.minimum((np.arange(width) * mask.shape[1] / width).astype(int), mask.shape[1] - 1)
    return mask[np.ix_(ys, xs)]


def _read_mask(path: Path) -> np.ndarray:
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as handle:
            image = ImageOps.exif_transpose(handle)
            if image.mode in ("RGBA", "LA"):
                alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
                # Sidecars commonly use transparent PNGs; use alpha as the
                # identity mask when it carries nontrivial coverage.
                if alpha.max(initial=0) > 0 and alpha.min(initial=255) < 255:
                    return alpha > 127
            return np.asarray(image.convert("L"), dtype=np.uint8) > 127
    except ImportError as exc:
        raise ObjectSupportError("Pillow is required to read object masks") from exc
    except (OSError, ValueError) as exc:
        raise MissingSupportEvidence(f"mask is unreadable: {path}") from exc


def load_mask_for_view(
    observation: SupportObservation,
    objects_dir: Path,
    view: Any,
    camera: Any,
) -> np.ndarray:
    """Load and map one sidecar mask into the ViewSet's calibrated image space."""
    path = resolve_contained(observation.mask_path, "mask_path", Path(objects_dir).resolve())
    literal = Path(objects_dir) / observation.mask_path
    if literal.is_symlink() or not path.is_file():
        raise MissingSupportEvidence(f"mask_path is not a regular file: {observation.mask_path!r}")
    actual = sha256_file(path)
    if observation.mask_sha256 and actual != observation.mask_sha256.lower():
        raise MissingSupportEvidence(
            f"mask checksum mismatch for {observation.mask_path!r}: "
            f"declared {observation.mask_sha256}, actual {actual}"
        )
    mask = _read_mask(path)
    if observation.mask_height and observation.mask_width and mask.shape != (observation.mask_height, observation.mask_width):
        raise MissingSupportEvidence(
            f"mask dimensions {mask.shape[1]}x{mask.shape[0]} disagree with recorded "
            f"{observation.mask_width}x{observation.mask_height}"
        )
    input_size = getattr(view, "rectification_input_size", None)
    if input_size is None:
        input_size = (view.width, view.height)
    input_width, input_height = (int(input_size[0]), int(input_size[1]))
    if observation.image_height and observation.image_width:
        if (observation.image_height, observation.image_width) != (input_height, input_width):
            raise MissingSupportEvidence(
                f"mask source dimensions {observation.image_width}x{observation.image_height} do not match "
                f"the calibrated image transform input {input_width}x{input_height}"
            )
    if observation.coordinate_space == "rectified":
        # A rectified mask already includes the sidecar's orientation/lens
        # transform.  There is no safe way to guess a crop or principal-point
        # shift from dimensions alone.
        if mask.shape != (view.height, view.width):
            raise MissingSupportEvidence(
                f"rectified mask for {observation.image_name!r} has shape {mask.shape}; "
                f"expected {(view.height, view.width)}"
            )
        transform = observation.transform or {}
        output_size = transform.get("output_size")
        if output_size is None and "width" in transform and "height" in transform:
            output_size = [transform["width"], transform["height"]]
        if tuple(int(item) for item in output_size) != (view.width, view.height):
            raise MissingSupportEvidence(
                f"rectified mask transform output {output_size} does not match "
                f"ViewSet {(view.width, view.height)}"
            )
        rectified_k = transform.get("intrinsics", transform.get("K"))
        actual_k = view.intrinsics.detach().cpu().numpy()
        if not np.allclose(np.asarray(rectified_k, dtype=np.float64), actual_k, atol=1e-3, rtol=1e-4):
            raise MissingSupportEvidence(
                f"rectified mask intrinsics for {observation.image_name!r} disagree with ViewSet"
            )
    else:
        # Raw/view masks follow the exact same pre-rectification input size as
        # ViewSet.  Resize only when the sidecar explicitly recorded those
        # source dimensions; otherwise a mismatch is ambiguous.
        if mask.shape != (input_height, input_width):
            if observation.image_height and observation.image_width:
                mask = resize_mask(mask, input_width, input_height)
            else:
                raise MissingSupportEvidence(
                    f"view mask for {observation.image_name!r} has shape {mask.shape}; "
                    f"expected calibrated input {(input_height, input_width)}"
                )

    # Sidecars may emit masks before the core's lens correction.  Applying the
    # same camera correction with nearest sampling keeps boundaries aligned.
    # A rectified mask already shares that transform and is only resized above.
    if observation.coordinate_space == "view" and getattr(camera, "has_distortion", False):
        try:
            import torch
            from . import undistort
            channels = torch.from_numpy(mask.astype(np.float32)).unsqueeze(-1).repeat(1, 1, 3)
            input_intrinsics = getattr(view, "rectification_input_intrinsics", None)
            if input_intrinsics is None:
                input_intrinsics = view.intrinsics
            corrected, corrected_k = undistort.undistort(
                channels, input_intrinsics.float(), camera, mode="nearest"
            )
            if corrected.shape[:2] != (view.height, view.width):
                raise MissingSupportEvidence(
                    f"rectified mask transform for {observation.image_name!r} produced "
                    f"{tuple(corrected.shape[:2])}, expected {(view.height, view.width)}"
                )
            final_k = view.intrinsics.detach().cpu().numpy()
            if not np.allclose(corrected_k.detach().cpu().numpy(), final_k, atol=1e-3, rtol=1e-4):
                raise MissingSupportEvidence(
                    f"mask transform intrinsics for {observation.image_name!r} disagree with ViewSet"
                )
            mask = corrected[..., 0].cpu().numpy() > 0.5
        except ImportError as exc:
            raise ObjectSupportError("torch is required to apply camera mask transforms") from exc
    elif observation.coordinate_space == "view":
        mask = resize_mask(mask, view.width, view.height)
    if mask.shape != (view.height, view.width):
        raise MissingSupportEvidence(
            f"mapped mask for {observation.image_name!r} has shape {mask.shape}, "
            f"expected {(view.height, view.width)}"
        )
    if not mask.any():
        raise MissingSupportEvidence(f"mask for {observation.image_name!r} is empty after transforms")
    return np.ascontiguousarray(mask, dtype=bool)


def bind_object_support(
    record: Mapping[str, Any],
    model: Any,
    views: Any,
    objects_dir: Path,
    *,
    max_views: int = 120,
    min_views: int = 3,
    object_centroid: np.ndarray | None = None,
) -> ObjectSupport:
    """Parse, select, and bind evidence to actual loaded ViewSet entries."""
    observations = parse_support_observations(record)
    selected, model_indices = select_supporting_observations(
        model, observations, max_views=max_views, min_views=min_views,
        object_centroid=object_centroid,
    )
    by_name = {}
    for index, view in enumerate(views.views):
        if view.name in by_name:
            raise ObjectSupportError(f"ViewSet contains duplicate image name {view.name!r}")
        by_name[view.name] = index
    view_indices: list[int] = []
    masks: dict[int, np.ndarray] = {}
    bound_observations: list[SupportObservation] = []
    for observation, model_index in zip(selected, model_indices):
        view_index = by_name.get(observation.image_name)
        if view_index is None:
            raise MissingSupportEvidence(
                f"registered image {observation.image_name!r} is missing from the loaded ViewSet"
            )
        view = views.views[view_index]
        camera = model.camera_for(model.images[model_index])
        mask = load_mask_for_view(observation, objects_dir, view, camera)
        view_indices.append(view_index)
        masks[view_index] = mask
        bound_observations.append(observation)
    coverage = {
        str(observation.image_id): float(masks[view_index].mean())
        for observation, view_index in zip(bound_observations, view_indices)
    }
    return ObjectSupport(
        object_id=str(record["object_id"]), observations=bound_observations,
        view_indices=view_indices, masks=masks, scene_scale=float(views.scene_scale),
        diagnostics={
            "selected_views": len(view_indices),
            "selected_image_ids": [o.image_id for o in bound_observations],
            "mask_area_fraction_by_image_id": coverage,
            "unseen_surface_status": "surfaces outside masks or cameras are not reconstructed",
        },
    )


def inspect_splat_geometry(
    data: Mapping[str, Any],
    scene_scale: float,
    *,
    object_id: str = "object",
    max_outlier_fraction: float = 0.02,
) -> dict[str, Any]:
    """Diagnose geometry before rendering; return robust bounds and kept rows.

    The returned bounds are for diagnostics/clipping only.  They are derived
    from quantiles and robust spread, never from contaminated raw extrema.
    Individual finite Gaussian rows are retained for evidence gating.  A
    candidate dominated by extreme scale/spatial rows is rejected, while a
    small diagnostic set is kept until mask/depth support can decide whether
    it is actually visible.
    """
    means = np.asarray(data.get("means"), dtype=np.float64)
    scales_log = np.asarray(data.get("scales"), dtype=np.float64)
    quats = np.asarray(data.get("quats"), dtype=np.float64)
    opacities = np.asarray(data.get("opacities"), dtype=np.float64)
    if means.ndim != 2 or means.shape[1] != 3 or not len(means):
        raise ObjectSupportError(f"{object_id}: Gaussian means are empty or not [N,3]")
    n = len(means)
    if scales_log.shape != (n, 3) or quats.shape != (n, 4) or opacities.reshape(-1).shape != (n,):
        raise ObjectSupportError(f"{object_id}: Gaussian arrays have inconsistent counts/shapes")
    finite_rows = np.isfinite(means).all(axis=1) & np.isfinite(scales_log).all(axis=1)
    finite_rows &= np.isfinite(quats).all(axis=1) & np.isfinite(opacities.reshape(-1))
    if not finite_rows.all():
        bad = int((~finite_rows).sum())
        raise ObjectSupportError(f"{object_id}: rejected {bad}/{n} Gaussians with non-finite geometry")
    scales = np.exp(scales_log)
    if not np.isfinite(scales).all() or (scales <= 0).any():
        raise ObjectSupportError(f"{object_id}: Gaussian scales are non-finite or non-positive")
    quat_norm = np.linalg.norm(quats, axis=1)
    if (quat_norm <= 1e-8).any():
        raise ObjectSupportError(f"{object_id}: Gaussian rotations contain zero-length quaternions")
    scene_scale = max(float(scene_scale), 1e-9)
    per_axis_median = np.median(means, axis=0)
    mad = np.median(np.abs(means - per_axis_median), axis=0)
    robust_sigma = np.maximum(1.4826 * mad, scene_scale * 1e-6)
    robust_z = np.abs(means - per_axis_median) / robust_sigma
    # A 12-sigma row is a spatial diagnostic, not a reason to throw away a
    # legitimate thin part.  We only fail when the candidate is dominated by
    # such rows.  The final mask/depth/multiview gate decides whether a row has
    # actual support; raw quantiles never delete source geometry.
    spatial_outlier = (robust_z > 12).any(axis=1)
    scale_ratio = scales.max(axis=1) / np.maximum(scales.min(axis=1), 1e-12)
    elongated = scale_ratio > 100.0
    too_large = scales.max(axis=1) > scene_scale * 0.5
    outlier_rows = spatial_outlier | too_large
    outlier_fraction = float(outlier_rows.mean())
    if outlier_fraction > max_outlier_fraction:
        raise ObjectSupportError(
            f"{object_id}: {outlier_rows.sum()}/{n} ({outlier_fraction:.1%}) Gaussians are spatial/scale outliers; "
            "object support is mixed or unbounded"
        )
    quantile_low = np.quantile(means[~outlier_rows], 0.005, axis=0) if (~outlier_rows).any() else means.min(axis=0)
    quantile_high = np.quantile(means[~outlier_rows], 0.995, axis=0) if (~outlier_rows).any() else means.max(axis=0)
    extent = np.maximum(quantile_high - quantile_low, scene_scale * 1e-6)
    margin = np.maximum(np.median(scales[~outlier_rows], axis=0) * 3 if (~outlier_rows).any() else 0, extent * 0.02)
    bounds_min = quantile_low - margin
    bounds_max = quantile_high + margin
    keep = ~outlier_rows
    diagnostics = {
        "object_id": object_id,
        "gaussians": n,
        "diagnostic_outliers": int(outlier_rows.sum()),
        "excluded_outliers": 0,
        "outlier_fraction": outlier_fraction,
        "elongated_gaussians": int(elongated.sum()),
        "extreme_scale_gaussians": int(too_large.sum()),
        "median_scale": np.median(scales, axis=0).tolist(),
        "max_scale": scales.max(axis=0).tolist(),
        "robust_bounds": [bounds_min.tolist(), bounds_max.tolist()],
        # All finite source rows remain eligible.  A mask/depth-consistency
        # pass, rather than a raw coordinate heuristic, performs any later
        # rejection and is recorded in the returned support diagnostics.
        "kept_count": n,
        "diagnostic_outlier_indices": np.flatnonzero(outlier_rows)[:128].tolist(),
        "status": "usable-with-diagnostics" if outlier_rows.any() or elongated.any() else "usable",
    }
    return diagnostics


def validate_splat_geometry(data: Mapping[str, Any], scene_scale: float, *, object_id: str = "object") -> dict[str, Any]:
    """Strict pre-render geometry gate returning diagnostics for the manifest."""
    diagnostics = inspect_splat_geometry(data, scene_scale, object_id=object_id)
    if diagnostics["gaussians"] < 3:
        raise ObjectSupportError(f"{object_id}: fewer than three Gaussian rows are available")
    return diagnostics


def _project_points(points: np.ndarray, world_to_camera: np.ndarray, intrinsics: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    camera = points @ np.asarray(world_to_camera[:3, :3], dtype=np.float64).T + np.asarray(world_to_camera[:3, 3], dtype=np.float64)
    z = camera[:, 2]
    fx, fy, cx, cy = [float(intrinsics[i, j]) for i, j in ((0, 0), (1, 1), (0, 2), (1, 2))]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = fx * camera[:, 0] / z + cx
        v = fy * camera[:, 1] / z + cy
    return u, v, z


def _support_counts(points: np.ndarray, support: ObjectSupport, views: Any) -> np.ndarray:
    """Count calibrated mask/depth observations for arbitrary world points."""
    points = np.asarray(points, dtype=np.float64)
    if any(index not in support.depth_maps for index in support.view_indices):
        missing = [index for index in support.view_indices if index not in support.depth_maps]
        raise MissingSupportEvidence(
            f"{support.object_id}: depth evidence is missing for ViewSet indices {missing[:5]}"
        )
    counts = np.zeros(len(points), dtype=np.int16)
    for view_index in support.view_indices:
        batch = views.full(view_index, max_long_edge=None)
        shape = support.view_shapes.get(view_index)
        if shape is not None and shape != (batch.height, batch.width):
            target_h, target_w = shape
            k = batch.intrinsics[0].cpu().numpy().copy()
            k[0, :] *= target_w / batch.width
            k[1, :] *= target_h / batch.height
        else:
            target_h, target_w = batch.height, batch.width
            k = batch.intrinsics[0].cpu().numpy()
        mask = support.mask_for(view_index, width=target_w, height=target_h)
        u, v, z = _project_points(points, batch.world_to_camera[0].cpu().numpy(), k)
        x, y = np.rint(u).astype(np.int64), np.rint(v).astype(np.int64)
        inside = np.isfinite(u) & np.isfinite(v) & np.isfinite(z) & (z > 0)
        inside &= (x >= 0) & (x < target_w) & (y >= 0) & (y < target_h)
        valid = np.zeros(len(points), dtype=bool)
        valid[inside] = mask[y[inside], x[inside]]
        depth = support.depth_maps[view_index]
        observed = np.full(len(points), np.nan, dtype=np.float64)
        observed[inside] = depth[y[inside], x[inside]]
        tolerance = np.maximum(np.abs(z) * 0.05, float(support.scene_scale) * 0.02)
        valid &= np.isfinite(observed) & (np.abs(z - observed) <= tolerance)
        counts += valid.astype(np.int16)
    return counts


def supported_vertex_mask(vertices: np.ndarray, support: ObjectSupport, views: Any, *, min_views: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return per-vertex mask/depth support counts using bound evidence."""
    vertices = np.asarray(vertices, dtype=np.float64)
    counts = _support_counts(vertices, support, views)
    threshold = support.min_consistent_views if min_views is None else max(1, int(min_views))
    return counts >= threshold, counts


def filter_multiview_points(
    points: np.ndarray,
    colours: np.ndarray,
    support: ObjectSupport,
    views: Any,
    *,
    min_views: int | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Keep fused points observed in multiple calibrated object views.

    A point is supported when its projection lands inside the exact mask and,
    when a rendered depth is available, agrees with that view's depth within a
    scale-relative tolerance.  This check is intentionally performed after
    silhouette and opacity gating; a pixel merely inside a mask cannot create
    support by itself.
    """
    points = np.asarray(points, dtype=np.float64)
    colours = np.asarray(colours)
    if points.ndim != 2 or points.shape[1] != 3 or len(points) != len(colours):
        raise ObjectSupportError("fused object points and colours have incompatible shapes")
    required = int(min_views if min_views is not None else support.min_consistent_views)
    required = max(1, min(required, len(support.view_indices)))
    counts = _support_counts(points, support, views)
    keep = counts >= required
    diagnostics = {
        "consistency_required_views": required,
        "points_before_consistency": int(len(points)),
        "points_after_consistency": int(keep.sum()),
        "consistency_fraction": float(keep.mean()) if len(keep) else 0.0,
        "support_count_min": int(counts.min()) if len(counts) else 0,
        "support_count_median": float(np.median(counts)) if len(counts) else 0.0,
    }
    return points[keep].astype(np.float32), colours[keep].astype(np.float32), diagnostics


def validate_mesh_support(vertices: np.ndarray, faces: np.ndarray, support: ObjectSupport, views: Any) -> dict[str, Any]:
    """Reject unsupported sheets, bridges, extents and disconnected outputs."""
    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all():
        raise ObjectSupportError(f"{support.object_id}: mesh contains non-finite vertices")
    if not len(vertices) or not len(faces):
        raise ObjectSupportError(f"{support.object_id}: mesher produced no surface")
    if support.observed_bounds is not None:
        lower, upper = support.observed_bounds
        observed_extent = np.maximum(np.asarray(upper) - np.asarray(lower), support.scene_scale * 1e-6)
        margin = np.maximum(observed_extent * 0.15, support.scene_scale * 0.03)
        if (vertices.min(axis=0) < lower - margin).any() or (vertices.max(axis=0) > upper + margin).any():
            raise ObjectSupportError(
                f"{support.object_id}: mesh extent exceeds mask-supported observed bounds; "
                "unseen surfaces or false bridges are not publishable"
            )
    supported, counts = supported_vertex_mask(vertices, support, views)
    support_fraction = float(supported.mean())
    if not np.all(supported):
        raise ObjectSupportError(
            f"{support.object_id}: only {support_fraction:.1%} of mesh vertices have mask/depth support; "
            "unsupported vertices cannot be published"
        )

    # Connected components are diagnosed without deleting anything.  A large
    # component with no supported vertices is a likely Poisson bridge/sheet.
    parent = np.arange(len(vertices), dtype=np.int64)
    size = np.ones(len(vertices), dtype=np.int64)
    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = int(parent[index])
        return index
    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if size[ra] < size[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        size[ra] += size[rb]
    for face in np.asarray(faces):
        if len(face) != 3:
            raise ObjectSupportError(f"{support.object_id}: mesh contains non-triangle faces")
        a, b, c = (int(value) for value in face)
        if min(a, b, c) < 0 or max(a, b, c) >= len(vertices):
            raise ObjectSupportError(f"{support.object_id}: mesh contains invalid face indices")
        union(a, b); union(b, c)
    components: dict[int, list[int]] = {}
    for index in range(len(vertices)):
        components.setdefault(find(index), []).append(index)
    unsupported_components = []
    for members in components.values():
        ratio = float(supported[members].mean())
        if len(members) >= max(8, int(len(vertices) * 0.05)) and ratio < 0.2:
            unsupported_components.append({"vertices": len(members), "support_fraction": ratio})
    if unsupported_components:
        raise ObjectSupportError(
            f"{support.object_id}: mesh contains {len(unsupported_components)} sizeable unsupported component(s)"
        )
    # Test face interiors as well as vertices.  Endpoints on a silhouette can
    # otherwise make Poisson's unsupported bridge look legitimate.  Sampling
    # vertices, edge midpoints and the centroid catches a sheet that crosses a
    # masked hole while remaining bounded by supported vertices.
    triangles = np.asarray(faces, dtype=np.int64)
    samples = np.concatenate([
        vertices[triangles[:, 0]], vertices[triangles[:, 1]], vertices[triangles[:, 2]],
        (vertices[triangles[:, 0]] + vertices[triangles[:, 1]]) / 2,
        (vertices[triangles[:, 1]] + vertices[triangles[:, 2]]) / 2,
        (vertices[triangles[:, 2]] + vertices[triangles[:, 0]]) / 2,
        vertices[triangles].mean(axis=1),
    ], axis=0)
    sample_counts = _support_counts(samples, support, views).reshape(7, len(triangles))
    face_supported = (sample_counts >= support.min_consistent_views).all(axis=0)
    face_fraction = float(face_supported.mean()) if len(face_supported) else 0.0
    if not np.all(face_supported):
        raise ObjectSupportError(
            f"{support.object_id}: only {face_fraction:.1%} of triangle interiors have mask/depth support; "
            "rejecting unsupported sheets or bridges"
        )
    return {
        "vertices": int(len(vertices)),
        "faces": int(len(faces)),
        "supported_vertices": int(supported.sum()),
        "support_fraction": support_fraction,
        "face_support_fraction": face_fraction,
        "min_view_support": int(counts.min()) if len(counts) else 0,
        "median_view_support": float(np.median(counts)) if len(counts) else 0.0,
        "components": len(components),
        "unsupported_components": unsupported_components,
        "status": "supported",
    }


def observed_bounds(points: np.ndarray, scene_scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Robust bounds from mask/depth-supported fused points."""
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ObjectSupportError("No finite mask-supported points are available for object bounds")
    low = np.quantile(points, 0.005, axis=0)
    high = np.quantile(points, 0.995, axis=0)
    extent = np.maximum(high - low, max(float(scene_scale), 1e-9) * 1e-6)
    margin = extent * 0.02
    return low - margin, high + margin


def evidence_file_sha256(path: Path) -> str:
    """Small public helper used by the sidecar importer and tests."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
