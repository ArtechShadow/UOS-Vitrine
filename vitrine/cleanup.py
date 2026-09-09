"""Conservative, opt-in cleanup of a Gaussian-splat PLY.

Cleanup writes a derivative candidate and leaves the trained master untouched.
The default operation removes only rows that are non-finite or below the
viewer opacity floor.  Spatial removal is available only when a caller passes
explicit absolute bounds or a scene-relative radius derived from a measured
scene scale.  In particular, there is no connected-component or thin-geometry
heuristic here: a disconnected sofa leg may be real preservation evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: object, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not np.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _bounds_array(value: object, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != (3,) or not np.isfinite(array).all():
        raise ValueError(f"{name} must contain three finite coordinates")
    return array


@dataclass(frozen=True)
class CleanupReport:
    """Audit record written beside a cleanup candidate."""

    source: str
    target: str
    manifest: str
    source_sha256: str
    output_sha256: str
    input_count: int
    retained_count: int
    dropped_nonfinite: int
    dropped_low_opacity: int
    dropped_outside_bounds: int
    opacity_floor: float
    scene_bounds: list[list[float]] | None
    scene_scale: float | None
    radius_multiple: float | None
    status: str = "candidate: requires held-out render and visual validation"

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "target": self.target,
            "manifest": self.manifest,
            "source_sha256": self.source_sha256,
            "output_sha256": self.output_sha256,
            "input_count": self.input_count,
            "retained_count": self.retained_count,
            "dropped_nonfinite": self.dropped_nonfinite,
            "dropped_low_opacity": self.dropped_low_opacity,
            "dropped_outside_bounds": self.dropped_outside_bounds,
            "opacity_floor": self.opacity_floor,
            "scene_bounds": self.scene_bounds,
            "scene_scale": self.scene_scale,
            "radius_multiple": self.radius_multiple,
            "status": self.status,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def _write_manifest(path: Path, report: CleanupReport) -> None:
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    temporary.write_text(report.to_json() + "\n", encoding="utf-8")
    # The output is a fresh candidate, so replacing a pre-existing manifest
    # would obscure which candidate was audited.  Refuse it just as we refuse
    # an existing target PLY.
    if path.exists():
        temporary.unlink(missing_ok=True)
        raise FileExistsError(f"Refusing to overwrite existing cleanup manifest: {path}")
    os.replace(temporary, path)


def cleanup_splat(
    source: Path,
    target: Path,
    *,
    scene_bounds: tuple[object, object] | None = None,
    scene_scale: float | None = None,
    radius_multiple: float | None = None,
    opacity_floor: float = 1 / 255,
    min_active: int = 16,
) -> CleanupReport:
    """Write a safe cleanup derivative from ``source`` to ``target``.

    Parameters
    ----------
    source, target:
        The master PLY and a new derivative path.  They must resolve to
        different files, and an existing target is never overwritten.
    scene_bounds:
        Optional ``(lower_xyz, upper_xyz)`` absolute bounds.  Bounds are
        applied only when explicitly supplied.
    scene_scale, radius_multiple:
        Optional scene-relative radial bound.  The centre is the median of
        active positions and the radius is ``scene_scale * radius_multiple``.
        Both values are required together; no fixed world-unit constant is
        introduced.
    opacity_floor:
        Activated opacity floor, matching the compact viewer export default.
    min_active:
        Refuse to publish a candidate with fewer active rows than this count.
    """

    source = Path(source)
    target = Path(target)
    if not source.is_file():
        raise FileNotFoundError(f"splat source does not exist: {source}")
    if source.resolve() == target.resolve():
        raise ValueError("cleanup target must differ from the master source")
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing cleanup candidate: {target}")
    manifest_path = target.with_suffix(".cleanup.json")
    if manifest_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing cleanup manifest: {manifest_path}")
    floor = _finite_number(opacity_floor, "opacity_floor")
    if not 0 <= floor <= 1:
        raise ValueError("opacity_floor must be between 0 and 1")
    if isinstance(min_active, bool) or not isinstance(min_active, int) or min_active < 1:
        raise ValueError("min_active must be a positive integer")

    lower = upper = None
    if scene_bounds is not None:
        if len(scene_bounds) != 2:
            raise ValueError("scene_bounds must be (lower_xyz, upper_xyz)")
        lower = _bounds_array(scene_bounds[0], "scene_bounds lower")
        upper = _bounds_array(scene_bounds[1], "scene_bounds upper")
        if not (lower < upper).all():
            raise ValueError("scene_bounds lower coordinates must be below upper coordinates")
    if scene_scale is not None:
        scene_scale = _finite_number(scene_scale, "scene_scale")
        if scene_scale <= 0:
            raise ValueError("scene_scale must be positive")
    if radius_multiple is not None:
        radius_multiple = _finite_number(radius_multiple, "radius_multiple")
        if radius_multiple <= 0:
            raise ValueError("radius_multiple must be positive")
        if scene_scale is None:
            raise ValueError("radius_multiple requires scene_scale")

    source_hash = _sha256(source)
    try:
        from plyfile import PlyData, PlyElement
    except ImportError as exc:  # pragma: no cover - dependency is in the train env
        raise RuntimeError("plyfile is required for splat cleanup") from exc

    ply = PlyData.read(str(source))
    if "vertex" not in ply:
        raise ValueError("splat PLY has no vertex element")
    vertices = ply["vertex"].data
    names = vertices.dtype.names or ()
    required = {"x", "y", "z", "opacity"}
    missing = sorted(required.difference(names))
    if missing:
        raise ValueError("splat PLY is missing required fields: " + ", ".join(missing))

    positions = np.column_stack([np.asarray(vertices[name], dtype=np.float64) for name in ("x", "y", "z")])
    logits = np.asarray(vertices["opacity"], dtype=np.float64)
    with np.errstate(over="ignore", invalid="ignore"):
        opacities = 1.0 / (1.0 + np.exp(-np.clip(logits, -80, 80)))
    finite = np.isfinite(positions).all(axis=1) & np.isfinite(logits) & np.isfinite(opacities)
    # Keep the complete structured record only when all numeric properties are
    # finite.  This prevents a cleanup candidate from preserving a corrupt SH,
    # scale, or quaternion value that a downstream viewer cannot consume.
    for name in names:
        if np.issubdtype(vertices.dtype[name], np.number):
            finite &= np.isfinite(vertices[name])
    active = finite & (opacities >= floor)
    dropped_nonfinite = int((~finite).sum())
    dropped_low_opacity = int((finite & ~active).sum())

    if int(active.sum()) < min_active:
        raise ValueError(
            f"cleanup found only {int(active.sum())} active Gaussians; minimum is {min_active}"
        )

    keep = active.copy()
    bounds_for_report: list[list[float]] | None = None
    if lower is not None and upper is not None:
        inside = (positions >= lower).all(axis=1) & (positions <= upper).all(axis=1)
        keep &= inside
        bounds_for_report = [lower.tolist(), upper.tolist()]
    if scene_scale is not None and radius_multiple is not None:
        centre = np.median(positions[active], axis=0)
        distance = np.linalg.norm(positions - centre, axis=1)
        keep &= distance <= scene_scale * radius_multiple
        if bounds_for_report is None:
            bounds_for_report = [
                (centre - scene_scale * radius_multiple).tolist(),
                (centre + scene_scale * radius_multiple).tolist(),
            ]
    dropped_outside_bounds = int((active & ~keep).sum())
    if int(keep.sum()) < min_active:
        raise ValueError(
            f"explicit cleanup bounds would leave only {int(keep.sum())} Gaussians; minimum is {min_active}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    elements = []
    for element in ply.elements:
        if element.name == "vertex":
            elements.append(PlyElement.describe(vertices[keep].copy(), "vertex"))
        else:
            # Splat masters normally contain only vertex data.  Retain any
            # ancillary PLY elements a caller supplied rather than silently
            # dropping them from a derivative.
            elements.append(element)
    try:
        PlyData(
            elements,
            text=False,
            byte_order="<",
            comments=list(getattr(ply, "comments", [])),
        ).write(str(temporary))
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite existing cleanup candidate: {target}")
        os.replace(temporary, target)
        output_hash = _sha256(target)
        if _sha256(source) != source_hash:
            target.unlink(missing_ok=True)
            raise RuntimeError("master source changed during cleanup; derivative was discarded")
        report = CleanupReport(
            source=str(source),
            target=str(target),
            manifest=str(manifest_path),
            source_sha256=source_hash,
            output_sha256=output_hash,
            input_count=len(vertices),
            retained_count=int(keep.sum()),
            dropped_nonfinite=dropped_nonfinite,
            dropped_low_opacity=dropped_low_opacity,
            dropped_outside_bounds=dropped_outside_bounds,
            opacity_floor=floor,
            scene_bounds=bounds_for_report,
            scene_scale=scene_scale,
            radius_multiple=radius_multiple,
        )
        _write_manifest(manifest_path, report)
        return report
    finally:
        temporary.unlink(missing_ok=True)
