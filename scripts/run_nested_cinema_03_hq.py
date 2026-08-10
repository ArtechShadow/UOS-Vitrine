"""Highest-quality Nested Cinema reconstruction from the expanded source tree.

Source roles (see also docs/ops-parallel-work.md + 5090-port-handoff.md):

  stills/           72×25 MP JPEG masters + 449 HEIC (high-res phones)
  polycam_frames/   Polycam RGB (~1024²) — coverage / SfM glue only
  video/            4K60 HEVC walkthrough — coverage between stills
  luma-derivative/  EXCLUDED (soft cloud keyframes)

Training recipe deliberately avoids workstation-archive crop=1600 (opacity
collapse). Targets the best measured 5090-safe HQ band:

  source_long_edge=3072  crop=1024  cap≈2.5M  iters=30k
  OPACITY_REG=0.002      MCMC        SH3

Usage::

    .venv\\Scripts\\python.exe scripts/run_nested_cinema_03_hq.py
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine.colmap_io import read_model  # noqa: E402
from vitrine.export import write_splat_file  # noqa: E402
from vitrine.ingest import ingest  # noqa: E402
from vitrine.profiles import Profile  # noqa: E402
from vitrine.sfm import run_sfm  # noqa: E402
import vitrine.train as train_module  # noqa: E402

RUN_NAME = "nested-cinema-03-hq"
RUN_DIR = ROOT / "runs" / RUN_NAME
SOURCE = ROOT / "source"

# HQ knobs — coupled; do not raise crop without watching alive %.
# Host has ~32 GB RAM; ViewSet holds every registered image at source_long_edge
# as float32 RGB. 3072×~1k views OOMs; 2304 with stills-heavy set fits.
SOURCE_LONG_EDGE = 2304
CROP = 1024
CAP_MAX = 2_500_000
ITERATIONS = 30_000
OPACITY_REG = 0.002
SCALE_REG = 0.01
COLMAP_LONG_EDGE = 3200
SH_DEGREE = 3

# Keep most high-res stills; polycam + video are coverage, not masters.
STILLS_BUDGET = 550
VIDEO_BUDGET = 120
VIDEO_EXTRACT_FPS = 4.0
POLYCAM_KEEP = 100  # prune after ingest (shared stills_budget would also cut stills)

INCLUDE = ["stills", "polycam_frames", "video"]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("nc03_hq")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "logs").mkdir(exist_ok=True)

    # Log file for the whole pipeline
    fh = logging.FileHandler(RUN_DIR / "logs" / "vitrine.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(fh)

    recipe = {
        "run": RUN_NAME,
        "include": INCLUDE,
        "source_long_edge": SOURCE_LONG_EDGE,
        "crop": CROP,
        "cap_max": CAP_MAX,
        "iterations": ITERATIONS,
        "opacity_reg": OPACITY_REG,
        "scale_reg": SCALE_REG,
        "colmap_long_edge": COLMAP_LONG_EDGE,
        "stills_budget": STILLS_BUDGET,
        "video_budget": VIDEO_BUDGET,
        "video_extract_fps": VIDEO_EXTRACT_FPS,
        "strategy": "mcmc",
        "notes": [
            "Excludes luma-derivative",
            "Avoids crop=1600 archive collapse",
            "Polycam frames are a separate camera group for coverage",
        ],
    }
    (RUN_DIR / "recipe.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    log.info("recipe: %s", recipe)

    # --- ingest ----------------------------------------------------------
    # Temporarily raise extract FPS for 4K video density.
    import vitrine.ingest as ingest_mod

    old_fps = ingest_mod.VIDEO_EXTRACT_FPS
    ingest_mod.VIDEO_EXTRACT_FPS = VIDEO_EXTRACT_FPS
    try:
        if (RUN_DIR / "ingest" / "ingest.json").is_file():
            log.info("ingest already present — skip (delete ingest/ to redo)")
        else:
            log.info("INGEST from %s include=%s", SOURCE, INCLUDE)
            report = ingest(
                SOURCE,
                RUN_DIR / "ingest",
                long_edge=COLMAP_LONG_EDGE,
                stills_budget=STILLS_BUDGET,
                video_budget=VIDEO_BUDGET,
                include=INCLUDE,
            )
            log.info(
                "ingest done: accepted=%s rejected=%s groups=%s",
                report.accepted,
                report.rejected,
                [g["name"] for g in report.groups],
            )
            for note in report.notes:
                log.info("ingest note: %s", note)
    finally:
        ingest_mod.VIDEO_EXTRACT_FPS = old_fps

    # --- sfm -------------------------------------------------------------
    if (RUN_DIR / "sfm" / "sparse_text" / "images.txt").is_file():
        log.info("SfM already present — skip")
    else:
        log.info("SFM starting (Docker COLMAP)…")
        t0 = time.time()
        sfm = run_sfm(
            RUN_DIR / "ingest" / "images",
            RUN_DIR / "sfm",
            max_image_size=COLMAP_LONG_EDGE,
            use_gpu=None,
        )
        (RUN_DIR / "sfm" / "sfm.json").write_text(
            json.dumps(sfm.describe(), indent=2), encoding="utf-8"
        )
        log.info(
            "SfM done in %.1f min: %d registered, %d cameras, %d points, gpu=%s",
            (time.time() - t0) / 60.0,
            sfm.registered_images,
            sfm.cameras,
            sfm.points,
            sfm.used_gpu,
        )

    # --- train -----------------------------------------------------------
    model_dir = RUN_DIR / "model"
    if (model_dir / "train.json").is_file():
        log.info("train.json already present — skip train")
    else:
        train_module.OPACITY_REG = OPACITY_REG
        train_module.SCALE_REG = SCALE_REG
        train_module.DENSIFICATION_STRATEGY = "mcmc"

        profile = Profile(
            name=RUN_NAME,
            source_long_edge=SOURCE_LONG_EDGE,
            crop=CROP,
            cap_max=CAP_MAX,
            iterations=ITERATIONS,
            sh_degree=SH_DEGREE,
            colmap_long_edge=COLMAP_LONG_EDGE,
            relative_throughput=32.0,
        )
        model = read_model(RUN_DIR / "sfm" / "sparse_text")
        log.info(
            "TRAIN %s — %d images, %d points, profile crop=%d source=%d cap=%d iters=%d op_reg=%s",
            RUN_NAME,
            len(model.images),
            len(model.points_xyz),
            CROP,
            SOURCE_LONG_EDGE,
            CAP_MAX,
            ITERATIONS,
            OPACITY_REG,
        )
        t0 = time.time()
        report = train_module.train(
            model,
            RUN_DIR / "ingest" / "images",
            model_dir,
            profile,
            seed=0,
            eval_every=2000,
            save_every=10000,
        )
        log.info(
            "train done in %.1f min — PSNR %.3f SSIM %.4f n_gauss %s peak_vram %.2f GB",
            (time.time() - t0) / 60.0,
            report.final_psnr,
            report.final_ssim,
            f"{report.n_gaussians:,}",
            report.peak_vram_gb,
        )

    # --- export + alive stats --------------------------------------------
    ply = model_dir / "scene.ply"
    splat = model_dir / "scene.splat"
    if ply.is_file() and not splat.is_file():
        write_splat_file(ply, splat)
        log.info("wrote %s", splat)
    elif splat.is_file():
        log.info("scene.splat already present")

    if ply.is_file():
        try:
            import numpy as np
            from plyfile import PlyData

            opacity_logits = np.asarray(PlyData.read(ply)["vertex"]["opacity"], dtype=np.float64)
            opacity = 1.0 / (1.0 + np.exp(-opacity_logits))
            live = int((opacity > 0.005).sum())
            alive = 100.0 * live / max(len(opacity), 1)
            stats = {
                "live_gaussians": live,
                "total_gaussians": int(len(opacity)),
                "alive_percent": round(alive, 3),
            }
            (model_dir / "alive_stats.json").write_text(
                json.dumps(stats, indent=2), encoding="utf-8"
            )
            log.info("alive: %s (%.1f%%)", f"{live:,}", alive)
        except Exception as exc:  # noqa: BLE001
            log.warning("alive stats failed: %s", exc)

    # Copy recipe next to model for the UI
    shutil.copy2(RUN_DIR / "recipe.json", model_dir / "candidate.json")
    log.info("DONE — open UI run %s", RUN_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
