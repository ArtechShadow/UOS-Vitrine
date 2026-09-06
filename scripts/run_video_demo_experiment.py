"""Isolated video-only demo reconstruction and controlled appearance experiment.

Prepare extracts only the explicitly named video and performs a fresh pose
solve. Train compares appearance on/off on those same poses, images and split.
This does not modify shipped profiles or accepted runs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def save(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")


def prepare(args) -> None:
    from vitrine.ingest import extract_video_frames, select_sharpest, stage_group, IngestReport
    from vitrine.sfm import run_sfm

    if args.run_dir.exists():
        raise ValueError(f"Refusing to overwrite existing run {args.run_dir}")
    source = args.source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source must be one video file")
    args.run_dir.mkdir(parents=True)
    ingest_dir = args.run_dir / "ingest"
    ingest_dir.mkdir()
    started = time.time()
    provenance = {"source": str(source), "source_bytes": source.stat().st_size,
                  "source_sha256": digest(source), "video_budget": 200,
                  "colmap_long_edge": 3200, "fresh_video_only_sfm": True}
    save(args.run_dir / "video-provenance.json", provenance)
    group = extract_video_frames(source, ingest_dir / "_video_frames" / source.stem)
    kept, rejected = select_sharpest(group, 200)
    accepted = stage_group(group, kept, ingest_dir / "images", long_edge=3200)
    group.paths = kept
    report = IngestReport(groups=[group.describe()], accepted=accepted,
                          rejected=len(rejected), rejected_examples=[
                              {"file": path.name, "sharpness": round(score, 1), "reason": reason}
                              for path, score, reason in sorted(rejected, key=lambda item: item[1])[:20]],
                          notes=["Only the explicit source video was extracted; no other capture input."])
    (ingest_dir / "ingest.json").write_text(report.to_json(), encoding="utf-8")
    save(args.run_dir / "preparation-progress.json", {"stage": "sfm", "ingest_seconds": time.time()-started})
    result = run_sfm(ingest_dir / "images", args.run_dir / "sfm", max_image_size=3200, use_gpu=None)
    save(args.run_dir / "sfm" / "sfm.json", result.describe())
    assert digest(source) == provenance["source_sha256"], "Source checksum changed during preparation"
    save(args.run_dir / "preparation-progress.json", {"stage": "complete", "seconds": time.time()-started,
                                                     "registered_images": result.registered_images})


def train(args) -> None:
    from vitrine.colmap_io import read_model
    from vitrine.dataset import ViewSet
    from vitrine.evaluate import evaluate_ply
    from vitrine.export import write_splat_file, render_previews
    from vitrine.profiles import Profile
    import vitrine.train as trainer
    import torch

    output = args.run_dir / "experiments" / ("appearance-" + args.appearance)
    if output.exists():
        raise ValueError(f"Refusing to overwrite existing experiment {output}")
    model = read_model(args.run_dir / "sfm" / "sparse_text")
    if len(model.cameras) != 1 or not all(i.name.startswith("video_") for i in model.images):
        raise ValueError("Expected a fresh one-camera video-only pose solve")
    output.mkdir(parents=True)
    handler = logging.FileHandler(output / "training.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    profile = Profile(name="video-demo-appearance-" + args.appearance, source_long_edge=2304,
                      crop=1536, cap_max=2_000_000, iterations=15000,
                      sh_degree=3, colmap_long_edge=3200, relative_throughput=32.0)
    trainer.APPEARANCE_OPT = args.appearance == "on"
    trainer.MAX_ANISOTROPY = 100.0
    trainer.MAX_SCALE_FRACTION = 0.25
    trainer.OPACITY_REG_UNTIL_REFINE_STOP = True
    trainer.LR_DECAY_HORIZON_STEPS = 15000
    trainer.DENSIFICATION_STRATEGY = "mcmc"
    trainer.EXPORT_CLAMP_STUDY = ()
    recipe = {"profile": asdict(profile), "appearance_opt": trainer.APPEARANCE_OPT,
              "seed": 0, "undistort": True, "max_anisotropy": 100,
              "max_scale_fraction": 0.25, "opacity_release": True,
              "torch": torch.__version__, "gpu": torch.cuda.get_device_name(0),
              "python": sys.version,
              "pipeline_sha256": {name: digest(ROOT / "vitrine" / name)
                                  for name in ("train.py", "dataset.py", "undistort.py", "export.py", "sfm.py", "ingest.py")},
              "pose_sha256": {name: digest(args.run_dir / "sfm" / "sparse_text" / name)
                              for name in ("cameras.txt", "images.txt", "points3D.txt")},
              "source_provenance": json.loads((args.run_dir / "video-provenance.json").read_text())}
    save(output / "recipe.json", recipe)
    views = ViewSet(model, args.run_dir / "ingest" / "images", long_edge=2304, device="cuda", undistort=True)
    save(output / "evaluation-split.json", {"eval_names": [views.views[i].name for i in views.eval_indices],
                                            "max_long_edge": 1600})
    trainer.train(model, args.run_dir / "ingest" / "images", output, profile,
                  seed=0, eval_every=2500, save_every=15000, views=views)
    result = evaluate_ply(output / "scene.ply", views, max_long_edge=1600)
    (output / "evaluation.json").write_text(result.to_json(), encoding="utf-8")
    write_splat_file(output / "scene.ply", output / "scene.splat")
    # This experiment has one video group: spread previews along the complete
    # held-out sequence instead of choosing only its first few frames.
    preview_count = min(6, len(views.eval_indices))
    selected = [views.eval_indices[round(i * (len(views.eval_indices) - 1) / max(preview_count - 1, 1))]
                for i in range(preview_count)]
    render_previews(output / "scene.ply", output / "previews", views, indices=selected, max_long_edge=1600)
    save(output / "artifact-hashes.json", {name: digest(output / name) for name in ("scene.ply", "scene.splat")})
    print(result.summary(), flush=True)


def solve(args) -> None:
    """Resume a prepared run after a Docker startup failure."""
    from vitrine.sfm import run_sfm
    if (args.run_dir / "sfm" / "sparse_text" / "images.txt").exists():
        raise ValueError("A pose solve already exists; refusing to overwrite it")
    provenance = json.loads((args.run_dir / "video-provenance.json").read_text())
    if digest(Path(provenance["source"])) != provenance["source_sha256"]:
        raise ValueError("Source video no longer matches recorded checksum")
    started = time.time()
    result = run_sfm(args.run_dir / "ingest" / "images", args.run_dir / "sfm",
                     max_image_size=3200, use_gpu=None)
    save(args.run_dir / "sfm" / "sfm.json", result.describe())
    save(args.run_dir / "preparation-progress.json", {"stage": "complete", "sfm_seconds": time.time()-started,
                                                     "registered_images": result.registered_images})


def compare(args) -> None:
    """Create measured and visual comparisons; do not select a winner silently."""
    from PIL import Image, ImageDraw, ImageFont
    paths = {mode: args.run_dir / "experiments" / ("appearance-" + mode) for mode in ("on", "off")}
    reports = {mode: json.loads((path / "evaluation.json").read_text()) for mode, path in paths.items()}
    per_view = {mode: {v["name"]: v for v in report["views"]} for mode, report in reports.items()}
    if per_view["on"].keys() != per_view["off"].keys():
        raise ValueError("Evaluation image sets differ; comparison is not matched")
    out = args.run_dir / "comparison"
    out.mkdir(exist_ok=True)
    summary = {
        "views": len(per_view["on"]), "evaluation_long_edge": 1600, "seed": 0,
        "appearance_on": {m: reports["on"]["overall_" + m] for m in ("psnr", "ssim")},
        "appearance_off": {m: reports["off"]["overall_" + m] for m in ("psnr", "ssim")},
        "delta_off_minus_on": {m: reports["off"]["overall_" + m] - reports["on"]["overall_" + m]
                               for m in ("psnr", "ssim")},
        "off_wins_by_view": {m: sum(per_view["off"][name][m] > per_view["on"][name][m]
                                    for name in per_view["on"]) for m in ("psnr", "ssim")},
        "limitation": "One training seed; timings include normal workstation/browser load."
    }
    save(out / "matched-evaluation.json", summary)
    sources = sorted((paths["off"] / "previews").glob("*_source.png"))
    if not sources:
        raise ValueError("No held-out preview images found")
    font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 17)
    for cropped in (False, True):
        tile_w, tile_h = (400, 400) if cropped else (480, 270)
        row_h = tile_h + 38
        canvas = Image.new("RGB", (tile_w * 3, 40 + row_h * len(sources)), "#11151b")
        draw = ImageDraw.Draw(canvas)
        for col, label in enumerate(("Held-out source (undistorted)", "Appearance ON", "Appearance OFF")):
            draw.text((col * tile_w + 8, 8), label, fill="white", font=font)
        for row, source in enumerate(sources):
            stem = source.name.removesuffix("_source.png")
            if digest(source) != digest(paths["on"] / "previews" / source.name):
                raise ValueError(f"Ground truth differs between candidates for {stem}")
            files = [source, paths["on"] / "previews" / (stem + "_render.png"),
                     paths["off"] / "previews" / (stem + "_render.png")]
            for col, path in enumerate(files):
                with Image.open(path) as image:
                    if cropped:
                        x, y = (image.width - tile_w) // 2, (image.height - tile_h) // 2
                        tile = image.crop((x, y, x + tile_w, y + tile_h))
                    else:
                        tile = image.resize((tile_w, tile_h), Image.Resampling.LANCZOS)
                    canvas.paste(tile, (col * tile_w, 40 + row * row_h))
                label = stem + (" / centre 400px crop" if cropped else "")
                draw.text((col * tile_w + 8, 42 + row * row_h + tile_h), label, fill="white", font=font)
        canvas.save(out / ("source-on-off-crops.png" if cropped else "source-on-off.png"))
    print(json.dumps(summary, indent=2), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["prepare", "solve", "train", "compare"])
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--appearance", choices=["on", "off"], default="off")
    args = parser.parse_args()
    if args.stage == "prepare" and args.source is None:
        parser.error("prepare requires --source naming one video")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    {"prepare": prepare, "solve": solve, "train": train, "compare": compare}[args.stage](args)


if __name__ == "__main__":
    main()
