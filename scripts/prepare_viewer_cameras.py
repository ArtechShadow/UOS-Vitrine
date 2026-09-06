"""Prepare viewer viewpoints from named registered COLMAP capture images.

Example::

    python scripts/prepare_viewer_cameras.py --run-dir runs/my-capture \
        --image video/frame_00100.jpg --label "Room overview"

Image and label arguments are paired by occurrence order. Omit all labels to
use image filenames. The optional --sparse-dir supports runs reusing another
run's pose solve. No source media or reconstruction geometry is modified.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine.colmap_io import read_model, scene_scale  # noqa: E402


def prepare(run_dir: Path, names: list[str], labels: list[str] | None = None,
            sparse_dir: Path | None = None) -> Path:
    """Write validated viewpoints only after every requested image resolves."""
    if not run_dir.is_dir():
        raise ValueError(f"Run directory does not exist: {run_dir}")
    if not names:
        raise ValueError("At least one registered image is required")
    if labels is not None and len(labels) != len(names):
        raise ValueError("Supply one --label per --image, or omit all labels")
    sparse_dir = sparse_dir or run_dir / "sfm" / "sparse_text"
    model = read_model(sparse_dir)
    images = {image.name: image for image in model.images}
    unknown = [name for name in names if name not in images]
    if unknown:
        raise ValueError(f"Images not registered in {sparse_dir}: {', '.join(unknown)}")
    distance = scene_scale(model) / 3.0
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError("Registered camera positions have no finite positive scene scale")

    viewpoints = []
    for index, name in enumerate(names):
        image = images[name]
        camera = model.camera_for(image)
        if not math.isfinite(camera.fy) or camera.fy <= 0 or camera.height <= 0:
            raise ValueError(f"Invalid camera focal length or height for {name}")
        rotation = image.rotation_matrix()
        position = image.camera_centre()
        forward = rotation.T @ np.array([0.0, 0.0, 1.0])
        up = rotation.T @ np.array([0.0, -1.0, 0.0])
        look_at = position + forward * distance
        fov = math.degrees(2 * math.atan(camera.height / (2 * camera.fy)))
        if not all(np.isfinite(vector).all() for vector in (position, look_at, up)):
            raise ValueError(f"Nonfinite camera pose for {name}")
        if not 0 < fov < 180 or np.linalg.norm(up) < 1e-6:
            raise ValueError(f"Invalid camera orientation or field of view for {name}")
        viewpoints.append({
            "name": name,
            "label": labels[index] if labels is not None else Path(name).stem,
            "position": position.tolist(),
            "lookAt": look_at.tolist(),
            "up": up.tolist(),
            "verticalFov": fov,
        })

    output = run_dir / "model" / "viewer-cameras.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(viewpoints, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--image", required=True, action="append", help="Exact registered image name; repeat for each view")
    parser.add_argument("--label", action="append", help="Display label paired with each --image")
    parser.add_argument("--sparse-dir", type=Path, help="Override the run's sfm/sparse_text directory")
    args = parser.parse_args()
    try:
        output = prepare(args.run_dir, args.image, args.label, args.sparse_dir)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"Saved {len(args.image)} registered viewpoints to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
