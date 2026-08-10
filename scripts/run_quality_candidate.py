"""Run an isolated RTX 5090 quality candidate against the solved scene.

This intentionally does not mutate the named laptop or workstation profiles.
Each candidate writes into its own ``runs/<name>/model`` directory and records
the experimental controls alongside the normal training report.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
from plyfile import PlyData

from vitrine.colmap_io import read_model
from vitrine.export import write_splat_file
from vitrine.profiles import Profile
import vitrine.train as train_module


ROOT = Path(__file__).resolve().parents[1]
SOURCE_RUN = ROOT / "runs" / "nested-cinema-01-5090"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--source", type=int, required=True)
    parser.add_argument("--crop", type=int, required=True)
    parser.add_argument("--cap", type=int, required=True)
    parser.add_argument("--iterations", type=int, default=15_000)
    parser.add_argument("--opacity-reg", type=float, required=True)
    parser.add_argument("--scale-reg", type=float, default=0.01)
    parser.add_argument("--max-anisotropy", type=float, default=None)
    parser.add_argument("--strategy", choices=("mcmc", "default"), default="mcmc")
    parser.add_argument(
        "--include-group",
        default=None,
        help="optional COLMAP image-name prefix to isolate one camera group",
    )
    parser.add_argument("--seed", type=int, default=0)
    return parser.parse_args()


def alive_stats(path: Path) -> tuple[int, float]:
    opacity_logits = np.asarray(PlyData.read(path)["vertex"]["opacity"], dtype=np.float64)
    opacity = 1.0 / (1.0 + np.exp(-opacity_logits))
    live = int((opacity > 0.005).sum())
    return live, 100.0 * live / max(len(opacity), 1)


def main() -> None:
    args = parse_args()
    output_dir = ROOT / "runs" / args.name / "model"
    if (output_dir / "train.json").exists():
        raise SystemExit(f"refusing to overwrite completed candidate: {output_dir}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    train_module.OPACITY_REG = args.opacity_reg
    train_module.SCALE_REG = args.scale_reg
    train_module.MAX_ANISOTROPY = args.max_anisotropy
    train_module.DENSIFICATION_STRATEGY = args.strategy

    model = read_model(SOURCE_RUN / "sfm" / "sparse_text")
    if args.include_group:
        prefix = args.include_group.rstrip("/\\") + "/"
        model.images = [image for image in model.images if image.name.replace("\\", "/").startswith(prefix)]
        used_camera_ids = {image.camera_id for image in model.images}
        model.cameras = {
            camera_id: camera
            for camera_id, camera in model.cameras.items()
            if camera_id in used_camera_ids
        }
        if not model.images:
            raise SystemExit(f"no registered images in group {args.include_group!r}")
        logging.info("candidate restricted to %d images in %s", len(model.images), args.include_group)
    profile = Profile(
        name=args.name,
        source_long_edge=args.source,
        crop=args.crop,
        cap_max=args.cap,
        iterations=args.iterations,
        sh_degree=3,
        colmap_long_edge=3200,
        relative_throughput=32.0,
    )
    report = train_module.train(
        model,
        SOURCE_RUN / "ingest" / "images",
        output_dir,
        profile,
        seed=args.seed,
    )
    splat_path = write_splat_file(output_dir / "scene.ply", output_dir / "scene.splat")
    live, alive_percent = alive_stats(output_dir / "scene.ply")
    precise_cost = report.energy_kwh * report.electricity_rate_gbp_per_kwh
    candidate = {
        "name": args.name,
        "source_long_edge": args.source,
        "crop": args.crop,
        "cap_max_requested": args.cap,
        "iterations": args.iterations,
        "opacity_reg": args.opacity_reg,
        "scale_reg": args.scale_reg,
        "max_anisotropy": args.max_anisotropy,
        "strategy": args.strategy,
        "include_group": args.include_group,
        "seed": args.seed,
        "live_gaussians": live,
        "alive_percent": round(alive_percent, 3),
        "energy_kwh": report.energy_kwh,
        "electricity_rate_gbp_per_kwh": report.electricity_rate_gbp_per_kwh,
        "cost_gbp_precise": round(precise_cost, 5),
        "scene_splat": str(splat_path),
    }
    (output_dir / "candidate.json").write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    print(json.dumps({**json.loads(report.to_json()), **candidate}, indent=2))


if __name__ == "__main__":
    main()
