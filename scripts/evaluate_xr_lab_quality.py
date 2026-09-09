"""Compare saved XR Lab candidates against identical held-out image tensors."""
from pathlib import Path
import argparse
import json
import logging
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine.colmap_io import read_model
from vitrine.dataset import ViewSet
from vitrine.evaluate import evaluate_ply
from vitrine.evaluation_preview import EvaluationPreview
from vitrine.construction import Progress, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", nargs="+", help="Experiment names under the XR Lab run")
    parser.add_argument("--checkpoints", action="store_true", help="Also score saved intermediate PLYs")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    run = ROOT / "runs" / "xr-lab-rtx-a6000-video-test"
    targets = {"baseline": run / "model"}
    for name in args.candidate:
        if Path(name).name != name or name in {".", "..", "baseline"}:
            parser.error("Expected a simple experiment name")
        targets[name] = run / "experiments" / name / "model"
    for folder in targets.values():
        if not (folder / "scene.ply").is_file():
            raise SystemExit(f"Missing completed model: {folder}")
    models = {name: folder / "scene.ply" for name, folder in targets.items()}
    if args.checkpoints:
        for name, folder in targets.items():
            if name != "baseline":
                for checkpoint in sorted(folder.glob("checkpoint_*.ply")):
                    models[f"{name}-{checkpoint.stem}"] = checkpoint
    output = run / "quality-comparisons"
    output.mkdir(exist_ok=True)
    # Match the original evaluation's reference preparation. All candidates
    # use the same tensors, holdout indices, intrinsics and render resolution.
    views = ViewSet(read_model(run / "sfm" / "sparse_text"),
                    run / "ingest" / "images", long_edge=3200,
                    device="cuda", undistort=True)
    results = {}
    for name, ply in models.items():
        destination = output / name
        with Progress(destination, "evaluate") as observer:
            preview = EvaluationPreview(destination / "evaluation-previews", observer)
            report = evaluate_ply(ply, views,
                                  max_long_edge=1600, on_view=preview)
            if len(preview.records) != len(views.eval_indices):
                raise RuntimeError("Incomplete visual evidence")
            atomic_json(destination / "evaluation.json", json.loads(report.to_json()))
        results[name] = {"psnr": report.overall_psnr, "ssim": report.overall_ssim,
                         "views": len(report.views), "ply": str(ply)}
        print(name, report.summary(), flush=True)
    atomic_json(output / "comparison.json", results)


if __name__ == "__main__":
    main()
