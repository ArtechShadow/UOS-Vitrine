"""Compare Gaussian opacity and anisotropy health between PLY models."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from plyfile import PlyData


#: Below this opacity a Gaussian contributes nothing to any render, and the
#: photometric loss therefore stops constraining its shape. Their scales drift
#: wherever regularisation pushes them, so a shape statistic taken over the
#: whole file describes the debris, not the model. Every shape figure here is
#: reported over the live population as well as the raw one.
ALIVE_THRESHOLD = 0.005


def summarize(path: Path) -> dict[str, float | int]:
    vertices = PlyData.read(str(path))["vertex"].data
    opacity_logit = np.asarray(vertices["opacity"], dtype=np.float64)
    opacity = 1.0 / (1.0 + np.exp(-opacity_logit))
    scales = np.column_stack(
        [np.asarray(vertices[f"scale_{axis}"], dtype=np.float64) for axis in range(3)]
    )
    anisotropy = np.exp(scales.max(axis=1) - scales.min(axis=1))
    alive = opacity > ALIVE_THRESHOLD
    live_anisotropy = anisotropy[alive] if alive.any() else anisotropy[:0]

    summary: dict[str, float | int] = {
        "gaussians": int(len(vertices)),
        "live_gaussians": int(alive.sum()),
        "opacity_median": float(np.median(opacity)),
        "alive_percent": float(alive.mean() * 100.0),
        "anisotropy_median_all": float(np.median(anisotropy)),
        "anisotropy_p99_all": float(np.quantile(anisotropy, 0.99)),
    }
    if len(live_anisotropy):
        summary["anisotropy_median_live"] = float(np.median(live_anisotropy))
        summary["anisotropy_p90_live"] = float(np.quantile(live_anisotropy, 0.90))
        summary["anisotropy_p99_live"] = float(np.quantile(live_anisotropy, 0.99))
        live_scales = np.exp(scales[alive])
        summary["radius_median_live"] = float(np.median(live_scales.max(axis=1)))
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    print(json.dumps({str(path): summarize(path) for path in args.paths}, indent=2))


if __name__ == "__main__":
    main()
