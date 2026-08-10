"""The master Nested Cinema reconstruction — every measured decision applied.

Reuses the `nested-cinema-03-hq` pose solve (736 of 741 views registered,
404,570 sparse points, 5 camera groups), which is the strongest this capture
has produced and cost 35 minutes of COLMAP. Nothing found since changes the
poses; the findings are all downstream of them.

Every knob below is a measurement, not a preference. The sweep that produced
them is `scripts/sweep_recipes.py`; the results are in
`docs/nested-cinema-04-master.md`.

  undistort           +4.93 dB / +0.155 SSIM. The single largest fix — the
                      rasteriser is a pinhole projector and no view had ever
                      been corrected for the lens. See docs/undistortion-finding.md.
  crop 1024           +1.44 dB over 768, *once* undistorted. Larger crops were
                      previously believed harmful; they were reaching into the
                      uncorrected corners.
  no appearance model +1.17 dB over having one. Held-out views are scored at
                      identity, and the per-view gains drift the canonical
                      exposure away from them. Reverted on the measurement.
  cap 2M              +0.5 dB over 1M with the regularisation cap-scaled.
  opacity reg release +0.7 dB exported, alive 53% -> 77%. The penalty drives
                      MCMC's birth-and-death process; past refine_stop there is
                      no birth left, so it only subtracts.
  anisotropy <= 100   +1.1 dB exported. Bounds needle splats that fit the
                      training views and break between them.
  max_scale 0.25      +3.2 dB exported over the old 0.05 ceiling, which was
                      clipping walls and ceilings along with real offenders.
  2304 / crop 1536    22.82 dB, the best measured. Resolution is bounded by
                      *frame coverage*, not by the GPU: a crop must still cover
                      ~2/3 of a frame or the regularisers outrun the
                      reconstruction gradient and the model collapses. 2304
                      with a 1536 crop holds 67% coverage; 3072 with the same
                      crop drops to 45% and falls apart.
  fixed LR horizon    30k steps beat 15k by 0.6 dB, but stretching the decay
                      horizon to match lost 0.2 dB. Left at 15k.

Usage::

    .venv\\Scripts\\python.exe scripts/run_nested_cinema_04_master.py
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine.colmap_io import read_model  # noqa: E402
from vitrine.dataset import ViewSet  # noqa: E402
from vitrine.export import write_splat_file  # noqa: E402
from vitrine.profiles import Profile  # noqa: E402
import vitrine.train as train_module  # noqa: E402

# Overridable so a challenger at a different resolution can be run against the
# same everything-else without editing the recipe.
RUN_NAME = os.environ.get("VITRINE_RUN_NAME", "nested-cinema-04-master")
RUN_DIR = ROOT / "runs" / RUN_NAME
SOURCE_RUN = ROOT / "runs" / "nested-cinema-03-hq"  # ingest + SfM come from here

# The view cache holds every registered view at once. Storing it as uint8
# rather than float32 cut it fourfold — 736 views at 2304 px now cost 5.6 GB
# instead of ~31 GB — so RAM is no longer what picks this number. Frame
# coverage is (see the table in the module docstring above).
SOURCE_LONG_EDGE = int(os.environ.get("VITRINE_SOURCE_LONG_EDGE", 2304))
CROP = int(os.environ.get("VITRINE_CROP", 1536))
CAP_MAX = 2_000_000
ITERATIONS = 30_000
LR_HORIZON = 15_000
SH_DEGREE = 3
MAX_ANISOTROPY = 100.0
MAX_SCALE_FRACTION = 0.25


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    (RUN_DIR / "logs").mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    handler = logging.FileHandler(RUN_DIR / "logs" / "vitrine.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    log = logging.getLogger("nc04")

    # Point at the existing ingest/SfM rather than copying 700 staged images.
    images_dir = SOURCE_RUN / "ingest" / "images"
    sparse_dir = SOURCE_RUN / "sfm" / "sparse_text"
    if not (sparse_dir / "images.txt").is_file():
        log.error("no SfM at %s — run scripts/run_nested_cinema_03_hq.py first", sparse_dir)
        return 1

    recipe = {
        "run": RUN_NAME,
        "sfm_from": str(SOURCE_RUN.relative_to(ROOT)),
        "source_long_edge": SOURCE_LONG_EDGE,
        "crop": CROP,
        "cap_max": CAP_MAX,
        "iterations": ITERATIONS,
        "lr_decay_horizon": LR_HORIZON,
        "sh_degree": SH_DEGREE,
        "undistort": True,
        "appearance_opt": False,
        "max_anisotropy": MAX_ANISOTROPY,
        "max_scale_fraction": MAX_SCALE_FRACTION,
        "opacity_reg_released_after_refine_stop": True,
        "strategy": "mcmc",
    }
    (RUN_DIR / "recipe.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
    log.info("recipe: %s", recipe)

    train_module.MAX_ANISOTROPY = MAX_ANISOTROPY
    train_module.MAX_SCALE_FRACTION = MAX_SCALE_FRACTION
    train_module.OPACITY_REG_UNTIL_REFINE_STOP = True
    train_module.APPEARANCE_OPT = False
    train_module.LR_DECAY_HORIZON_STEPS = LR_HORIZON
    train_module.DENSIFICATION_STRATEGY = "mcmc"
    train_module.EXPORT_CLAMP_STUDY = (0.10, 0.25, 1.00)

    model = read_model(sparse_dir)
    log.info("model: %s", model.summary())

    log.info("loading %d views at long edge %d, undistorted", len(model.images), SOURCE_LONG_EDGE)
    t0 = time.time()
    views = ViewSet(model, images_dir, long_edge=SOURCE_LONG_EDGE, device="cuda", undistort=True)
    log.info(
        "views ready in %.1f min — %.2f GB cache", (time.time() - t0) / 60, views.memory_footprint_gb()
    )

    profile = Profile(
        name=RUN_NAME,
        source_long_edge=SOURCE_LONG_EDGE,
        crop=CROP,
        cap_max=CAP_MAX,
        iterations=ITERATIONS,
        sh_degree=SH_DEGREE,
        colmap_long_edge=3200,
        relative_throughput=32.0,
    )
    model_dir = RUN_DIR / "model"
    t0 = time.time()
    report = train_module.train(
        model, images_dir, model_dir, profile,
        seed=0, eval_every=2500, save_every=10_000, views=views,
    )
    log.info(
        "train done in %.1f min — PSNR %.3f (%.3f exported) SSIM %.4f alive %.1f%% aniso %.0f",
        (time.time() - t0) / 60, report.final_psnr, report.export_psnr,
        report.final_ssim, report.alive_fraction * 100, report.live_anisotropy_median,
    )

    ply = model_dir / "scene.ply"
    write_splat_file(ply, model_dir / "scene.splat")
    shutil.copy2(RUN_DIR / "recipe.json", model_dir / "candidate.json")
    log.info("DONE — %s", model_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
