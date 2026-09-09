"""Create a conservative cleanup candidate, preserving every retained SH field.

No connected-component pruning: disconnected furniture can be genuine.
Low-opacity isolation is a hypothesis that must pass held-out/visual checks.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from plyfile import PlyData, PlyElement
from scipy.spatial import cKDTree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Refusing to overwrite an existing cleanup candidate")
    source_hash = hashlib.sha256(args.source.read_bytes()).hexdigest()
    ply = PlyData.read(args.source)
    vertices = ply["vertex"].data
    positions = np.column_stack([vertices[k] for k in ("x", "y", "z")])
    opacity = 1 / (1 + np.exp(-np.clip(vertices["opacity"], -80, 80)))
    finite = np.isfinite(positions).all(axis=1) & np.isfinite(opacity)
    active = finite & (opacity >= 1 / 255)
    if active.sum() < 16:
        raise SystemExit("Insufficient active geometry")
    centre = np.median(positions[active], axis=0)
    radius = np.linalg.norm(positions - centre, axis=1)
    median_radius = float(np.median(radius[active]))
    if median_radius <= 0:
        raise SystemExit("Degenerate geometry")
    near = radius <= 12 * median_radius
    indices = np.flatnonzero(active & near)
    distances, _ = cKDTree(positions[indices]).query(positions[indices], k=9, workers=2)
    spacing = distances[:, -1]
    typical = float(np.median(spacing))
    # Only remove a weak splat if its neighbourhood is exceptionally sparse.
    isolated = (spacing > max(8 * typical, .02 * median_radius)) & (opacity[indices] < .05)
    keep = active & near
    keep[indices[isolated]] = False
    args.output.parent.mkdir(parents=True, exist_ok=True)
    PlyData([PlyElement.describe(vertices[keep].copy(), "vertex")],
            text=False, byte_order="<", comments=list(ply.comments)).write(args.output)
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != source_hash:
        raise RuntimeError("Source changed during cleanup")
    report = dict(source=str(args.source), source_sha256=source_hash,
                  input=len(vertices), retained=int(keep.sum()),
                  inactive_or_invalid=int((~active).sum()),
                  distant=int((active & ~near).sum()), isolated_weak=int(isolated.sum()),
                  median_radius=median_radius, median_neighbour_spacing=typical,
                  radius_multiple=12, isolation_multiple=8, weak_opacity=.05,
                  status="candidate: requires render and visual validation")
    args.output.with_suffix(".cleanup.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
