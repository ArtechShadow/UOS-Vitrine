"""Train several recipes against one loaded view cache and rank them.

Decoding, resizing and undistorting 736 views costs minutes and tens of
gigabytes of RAM. Paying that once and reusing the :class:`ViewSet` across
candidates is what makes a real comparison affordable — and it removes data
loading as a variable between them.

The sweep runs at a reduced ``source_long_edge`` so a candidate costs single
figure minutes. It is for *ranking* recipes, not for producing the master; the
winner is then re-run at full resolution.

Usage::

    .venv\\Scripts\\python.exe scripts/sweep_recipes.py [run-dir]
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine.colmap_io import read_model  # noqa: E402
from vitrine.dataset import ViewSet  # noqa: E402
from vitrine.profiles import Profile  # noqa: E402
import vitrine.train as train_module  # noqa: E402

#: Ranking runs use a reduced source resolution so a candidate costs single
#: figure minutes. Override when a decision is resolution-dependent — the crop
#: optimum is, since a crop is a window *of* the source.
SWEEP_LONG_EDGE = int(os.environ.get("VITRINE_SWEEP_LONG_EDGE", 1600))
SWEEP_ITERATIONS = 15_000


@dataclass
class Candidate:
    """Defaults are the best configuration measured so far.

    ``crop`` 1024 and ``appearance`` off are results, not guesses: see the
    ``structure`` set below. Later sets are therefore ranked against candidate
    (d), which is what these defaults reproduce.
    """

    name: str
    crop: int = 1024
    cap_max: int = 1_000_000
    iterations: int = SWEEP_ITERATIONS
    opacity_reg: float = 0.01
    scale_reg: float = 0.01
    appearance: bool = False
    undistort: bool = True
    sh_degree: int = 3
    max_anisotropy: float | None = None
    release_opacity_reg: bool = False
    lr_horizon: int = 15_000
    note: str = ""
    result: dict = field(default_factory=dict)


# Each candidate changes one thing against candidate (a), which is the proven
# laptop-standard recipe applied to the expanded capture. (e) comes last
# because it needs the distorted view cache and only one cache fits in RAM.
SETS: dict[str, list[Candidate]] = {
    # Ran first, against the old defaults (crop 768, appearance on), which is
    # what candidate (a) is. Kept for reproducibility.
    "structure": [
        Candidate("a-proven-recipe", crop=768, appearance=True,
                  note="laptop-standard knobs + undistort + appearance"),
        Candidate("b-no-appearance", crop=768, note="isolates the exposure model"),
        Candidate("c-cap-2m", crop=768, appearance=True, cap_max=2_000_000,
                  note="more capacity, reg auto-scaled"),
        Candidate("d-crop-1024", appearance=True, note="wider window per step"),
        Candidate("e-no-undistort", crop=768, appearance=True, undistort=False,
                  note="isolates the lens correction"),
    ],
    # How hard a needle splat may be squeezed. The Luma reference sits at a
    # median ratio of 12 and a p99 of 418, so 30 and 100 bracket it; 1000 is a
    # loose guard that only catches the truly degenerate.
    "anisotropy": [
        Candidate("f0-control", note="new defaults, no guard — the control for f/g/h"),
        Candidate("f-aniso-1000", max_anisotropy=1000.0, note="loose guard"),
        Candidate("g-aniso-100", max_anisotropy=100.0, note="near Luma p99"),
        Candidate("h-aniso-30", max_anisotropy=30.0, note="near Luma p90"),
    ],
    # Whether the opacity penalty should outlive densification, on top of the
    # anisotropy guard that the previous set settled at 100.
    "schedule": [
        Candidate("i-aniso-only", max_anisotropy=100.0, note="control: the anisotropy winner"),
        Candidate("j-release-reg", max_anisotropy=100.0, release_opacity_reg=True,
                  note="opacity reg off after refine_stop"),
        Candidate("k-release-reg-2m", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, note="release + more capacity"),
    ],
    # A longer run only pays if the schedules stretch with it. With the decay
    # horizon pinned at 15k, a 30k run spends its second half fine-tuning at a
    # floored learning rate and with densification long closed.
    # Run at the master's own resolution (VITRINE_SWEEP_LONG_EDGE=3072): a crop
    # is a window of the source, so how much of the frame 1024 px covers — and
    # therefore how often a given Gaussian is in view — depends on it.
    "crop": [
        Candidate("n-crop-1024", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, iterations=30_000, note="the 1600px winner, at full res"),
        Candidate("o-crop-1536", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, iterations=30_000, crop=1536,
                  note="crop scaled with the source"),
    ],
    # Bracketing the resolution at which the 3072 runs fell apart. Run with
    # VITRINE_SWEEP_LONG_EDGE=2304.
    "resolution": [
        Candidate("p-2304-crop1024", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, iterations=30_000, note="2304 source, 1024 crop"),
        Candidate("q-2304-crop1536", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, iterations=30_000, crop=1536,
                  note="2304 source, crop scaled to keep frame coverage"),
    ],
    "duration": [
        Candidate("l-30k-fixed-horizon", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, iterations=30_000,
                  note="30k steps, schedules still sized for 15k"),
        Candidate("m-30k-matched-horizon", max_anisotropy=100.0, release_opacity_reg=True,
                  cap_max=2_000_000, iterations=30_000, lr_horizon=30_000,
                  note="30k steps, schedules stretched to match"),
    ],
}


def main(run_dir: Path, set_name: str) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger("sweep")
    candidates = SETS[set_name]

    model = read_model(run_dir / "sfm" / "sparse_text")
    log.info("model: %s", model.summary())

    out_root = run_dir.parent / f"{run_dir.name}-sweep-{set_name}"
    out_root.mkdir(parents=True, exist_ok=True)

    # One cache at a time: two of these do not fit in 32 GB. Candidates are
    # visited grouped by the cache they need so it is built at most once each.
    views: ViewSet | None = None
    loaded_undistorted: bool | None = None

    for candidate in sorted(candidates, key=lambda c: not c.undistort):
        if loaded_undistorted != candidate.undistort:
            views = None  # drop the old cache before allocating the new one
            import gc

            gc.collect()
            log.info(
                "loading views at long edge %d (undistort=%s) — this is the slow part",
                SWEEP_LONG_EDGE, candidate.undistort,
            )
            t0 = time.time()
            views = ViewSet(
                model, run_dir / "ingest" / "images",
                long_edge=SWEEP_LONG_EDGE, device="cuda", undistort=candidate.undistort,
            )
            loaded_undistorted = candidate.undistort
            log.info(
                "views ready in %.1f min, %.2f GB",
                (time.time() - t0) / 60, views.memory_footprint_gb(),
            )
        active = views

        train_module.OPACITY_REG = candidate.opacity_reg
        train_module.SCALE_REG = candidate.scale_reg
        train_module.APPEARANCE_OPT = candidate.appearance
        train_module.MAX_ANISOTROPY = candidate.max_anisotropy
        train_module.OPACITY_REG_UNTIL_REFINE_STOP = candidate.release_opacity_reg
        train_module.LR_DECAY_HORIZON_STEPS = candidate.lr_horizon
        train_module.DENSIFICATION_STRATEGY = "mcmc"
        # Price the export ceiling against held-out views on every candidate.
        train_module.EXPORT_CLAMP_STUDY = (0.02, 0.10, 0.25, 1.00)

        profile = Profile(
            name=candidate.name,
            source_long_edge=SWEEP_LONG_EDGE,
            crop=candidate.crop,
            cap_max=candidate.cap_max,
            iterations=candidate.iterations,
            sh_degree=candidate.sh_degree,
            colmap_long_edge=3200,
            relative_throughput=32.0,
        )
        output_dir = out_root / candidate.name
        log.info("=== %s === %s", candidate.name, candidate.note)
        t0 = time.time()
        report = train_module.train(
            model, run_dir / "ingest" / "images", output_dir, profile,
            seed=0, eval_every=2500, save_every=0, views=active,
        )
        candidate.result = {
            "psnr": report.final_psnr,
            "ssim": report.final_ssim,
            "export_psnr": report.export_psnr,
            "export_ssim": report.export_ssim,
            "alive": report.alive_fraction,
            "scale_clamped": report.scale_clamped_fraction,
            "anisotropy": report.live_anisotropy_median,
            "n_gaussians": report.n_gaussians,
            "minutes": round((time.time() - t0) / 60, 1),
        }
        log.info("%s -> %s", candidate.name, candidate.result)

        # The PLY is 600 MB a piece and the sweep only needs the numbers.
        for stale in output_dir.glob("*.ply"):
            stale.unlink()

    ranked = sorted(candidates, key=lambda c: -c.result.get("export_psnr", 0))
    print(f"\n{'candidate':<20}{'PSNR':>8}{'SSIM':>9}{'exp.PSNR':>10}{'alive':>8}{'aniso':>13}{'min':>7}")
    for c in ranked:
        r = c.result
        print(
            f"{c.name:<20}{r['psnr']:>8.2f}{r['ssim']:>9.4f}{r['export_psnr']:>10.2f}"
            f"{r['alive'] * 100:>7.1f}%{r['anisotropy']:>13,.0f}{r['minutes']:>7.1f}"
        )

    summary = {c.name: {"note": c.note, **c.result} for c in candidates}
    (out_root / "sweep.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {out_root / 'sweep.json'}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    raise SystemExit(main(
        Path(args[0] if args else "runs/nested-cinema-03-hq"),
        args[1] if len(args) > 1 else "structure",
    ))
