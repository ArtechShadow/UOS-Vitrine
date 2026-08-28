"""Train a 3D Gaussian Splat from a COLMAP reconstruction.

The design follows the reference 3DGS recipe with three deliberate departures,
each chosen for this project's constraints:

**MCMC densification** (`3DGS as Markov Chain Monte Carlo
<https://arxiv.org/abs/2404.09591>`_) rather than gradient-threshold cloning.
It takes a hard ``cap_max``, so cost and memory are decided up front instead of
emerging from a threshold — which is what makes a laptop run predictable. It
also reaches better quality at a fixed splat budget. MCMC requires opacity and
scale regularisation in the objective; those terms are part of the method, not
optional extras.

**Random-crop rendering.** Each step rasterises a window of a high-resolution
view rather than a downscaled whole frame (see ``dataset``). Full-resolution
detail stays available to the optimiser at a fraction of the per-step cost.

**A held-out split, evaluated on whole frames.** Quality is reported, not
asserted. Crops would make the metric depend on which windows happened to be
sampled, so evaluation always uses complete views.

Schedules matter as much as the loss: position learning rate decays ~100x over
the run so geometry settles, and SH bands are introduced one at a time so the
model fits base colour before view-dependent effects.
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from . import cuda_toolkit
from .colmap_io import Model
from .dataset import ViewSet
from .losses import photometric_loss, psnr, ssim
from .ply import SH_C0, sh_coefficient_count, write_splat_ply
from .profiles import Profile

logger = logging.getLogger(__name__)

# --- Optimiser settings, from the reference implementation -------------------
# Position LR is scaled by scene extent: COLMAP units are arbitrary, so a fixed
# step size means something different in every reconstruction.
LR_MEANS_INIT = 1.6e-4
LR_MEANS_FINAL_RATIO = 0.01   # decay to 1% over the run
LR_SCALES = 5e-3
LR_QUATS = 1e-3
LR_OPACITIES = 5e-2
LR_SH0 = 2.5e-3
LR_SHN_RATIO = 1 / 20         # higher bands learn far more slowly

#: MCMC regularisation weights (gsplat's reference values for this strategy).
OPACITY_REG = 0.01
SCALE_REG = 0.01

#: The opacity penalty is a *mean* over Gaussians, so its weight was tuned
#: against a particular population size — gsplat's reference value assumes a
#: cap around a million. Raise the cap without touching it and the same pull
#: acts on a model whose per-Gaussian reconstruction gradient is spread far
#: thinner, and the population regularises itself to death: measured on this
#: capture, cap 1.5M at 0.01 finished with **4.8%** of Gaussians alive and
#: 13.5 dB, while cap 2.5M at 0.002 reached 21.7% and 19.8 dB. Scaling the
#: weight by the reference-to-actual cap ratio keeps the total pressure fixed
#: as the budget changes, so cap becomes a capacity decision rather than a
#: quality gamble.
OPACITY_REG_REFERENCE_CAP = 1_000_000
SCALE_REG_WITH_CAP = True

#: Release the opacity penalty once densification closes.
#:
#: The penalty is part of MCMC's birth-and-death process: it pushes Gaussians
#: toward transparency so that relocation can recycle the ones that stop
#: earning their place. After ``refine_stop`` there is no relocation left, so
#: the same pressure only subtracts — a Gaussian it kills is gone, and nothing
#: replaces it. The trace shows exactly that: alive holds at 97-98% through
#: densification and falls to 55% over the following 3,750 steps, which is the
#: model quietly discarding capacity during the phase meant to be refining it.
OPACITY_REG_UNTIL_REFINE_STOP = False

#: Smallest fraction of a frame one crop may cover before training is warned
#: about. See the coverage note in :func:`train`; 0.5 sits between the 45% that
#: collapsed and the 64% that did not.
MIN_SAFE_COVERAGE = 0.5

#: Per-image appearance compensation. Auto-exposure and auto-white-balance move
#: between shots — a 741-image capture spanning three phones and a walkthrough
#: video has no single exposure — but a splat has exactly one radiance field to
#: satisfy every view. Without somewhere to put that variation the optimiser
#: bakes the average into the geometry, which shows up as haze and ghosting.
#: Each *training* view therefore learns a 3-channel gain and bias applied to
#: the render before the loss. Held-out views are scored at identity, so this
#: cannot inflate the reported metric — it only stops exposure drift from being
#: charged to geometry. Measured on the untreated nested-cinema-03-hq model, a
#: best-fit per-view affine recovered 1.6 dB, which is the size of the error
#: this removes from the geometry's shoulders.
APPEARANCE_OPT = True
LR_APPEARANCE = 1e-3
APPEARANCE_REG = 1e-2  # pull toward identity; kills the global gain/brightness gauge

#: Optional experimental ceiling on the ratio between a Gaussian's largest
#: and smallest scale axis.  ``None`` preserves the reference MCMC behaviour.
#: The quality-candidate runner sets this explicitly while we benchmark the
#: pathological scale anisotropy observed on Nested Cinema against the Luma
#: reference PLY; named production profiles are unchanged until a candidate
#: passes both the metric and visual gates.
MAX_ANISOTROPY: float | None = None

#: Experimental strategy selector used by the isolated quality-candidate
#: runner.  Production profiles retain MCMC unless a measured candidate earns
#: a change to the named pipeline.
DENSIFICATION_STRATEGY = "mcmc"

# MCMC always grows to fill cap_max, whether or not the scene has that much
# to reconstruct — cap_max is a *throughput* number sized for the GPU, not a
# measure of scene complexity, and a profile has no idea in advance how many
# COLMAP points a given capture will produce. Measured on the Nested Cinema
# capture (single room, 272 images, ~112K COLMAP points): cap_max=1,000,000
# (~9x the point count) converged cleanly to 25.7 dB PSNR; cap_max=6,000,000
# (~54x — workstation-archive's number, sized purely from GPU throughput)
# left 98.7% of the final Gaussians at ~zero opacity and caused a
# mid-training scale explosion (scale-clamp fraction at export: 40% -> 69% ->
# 0.6%, i.e. blew up then partially recovered) the model never fully
# recovered from, finishing at 14.3 dB. 15x leaves real headroom over the
# proven 9x while landing nowhere near the 54x that broke it.
CAP_MAX_POINT_MULTIPLIER = 15
MIN_CAP_MAX = 400_000  # floor so a sparse scene doesn't get capped absurdly low

#: Introduce one new SH band every this many steps.
SH_WARMUP_INTERVAL = 1000

# Position LR decays to LR_MEANS_FINAL_RATIO over this many steps, regardless
# of profile.iterations. Previously the decay rate was tied to
# profile.iterations (gamma = RATIO ** (1/iterations)), which meant a
# 30,000-iteration profile decayed at *half the rate*, in absolute step
# terms, of the 15,000-iteration one it was actually validated against.
# Measured on workstation-archive (30,000 iters): position LR was still 6.8x
# the converged 15,000-iter run's value at step 12,500, and the model spent
# most of training oscillating instead of settling, only stabilising in the
# last ~2,000 steps — final PSNR 14 dB vs. 25.7 dB on the validated schedule.
# A fixed horizon means extra iterations buy more time fine-tuning at the
# fully-decayed rate instead of a slower decay to the same place.
LR_DECAY_HORIZON_STEPS = 15_000

#: Export ceiling on Gaussian radius, as a fraction of scene scale. A few
#: enormous Gaussians fog an entire viewer; expressing the limit relative to
#: the scene keeps it meaningful whatever units COLMAP chose.
#:
#: The old value, 0.05, was never priced. It is applied at export, so it
#: changes the file that ships without touching the model that gets reported —
#: and measured against held-out views it was throwing away most of the run:
#:
#:   fraction   PSNR    SSIM     Gaussians touched
#:   0.02      13.88   0.6253    7.16%
#:   0.05      18.21   0.6941    1.54%      <- previous default
#:   0.10      19.96   0.7660    0.44%
#:   0.25      21.36   0.7777    0.08%      <- now
#:   none      21.84   0.7782    0
#:
#: Large Gaussians are not automatically pathological: a wall, a ceiling or a
#: dim background genuinely is one big smooth surface, and 0.05 was clipping
#: those along with the handful of real offenders. 0.25 still bounds any single
#: splat to a quarter of the scene — which is all the guard was ever for — and
#: costs half a decibel instead of three and a half.
MAX_SCALE_FRACTION = 0.25

#: Alternative ceilings to price against held-out views at the end of a run.
#: Empty disables the study. See the ``export clamp`` lines in the log.
EXPORT_CLAMP_STUDY: tuple[float, ...] = ()

#: UK average electricity unit rate supplied for reporting — not a live lookup, and this
#: run's actual tariff may differ. Override with the VITRINE_ELECTRICITY_RATE_GBP
#: env var rather than editing this if your tariff is known and different.
DEFAULT_ELECTRICITY_RATE_GBP_PER_KWH = 0.2611


def electricity_rate_gbp_per_kwh() -> float:
    override = os.environ.get("VITRINE_ELECTRICITY_RATE_GBP")
    if override:
        try:
            return float(override)
        except ValueError:
            logger.warning("VITRINE_ELECTRICITY_RATE_GBP=%r is not a number; using the default", override)
    return DEFAULT_ELECTRICITY_RATE_GBP_PER_KWH


def gpu_power_watts() -> float | None:
    """Instantaneous GPU power draw via nvidia-smi, or ``None`` if unavailable.

    Deliberately sampled rather than assumed from the card's TDP: actual draw
    during training runs well under the RTX 5090's 575 W limit (see the
    module docstring's benchmark), and the point of tracking energy at all is
    to measure it, not guess it.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=power.draw", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


@dataclass
class EvalResult:
    step: int
    psnr: float
    ssim: float
    n_gaussians: int

    def line(self) -> str:
        return (
            f"step {self.step:>6}  PSNR {self.psnr:5.2f} dB  SSIM {self.ssim:.4f}  "
            f"{self.n_gaussians:,} Gaussians"
        )


@dataclass
class TrainReport:
    profile: str
    iterations: int
    n_train_views: int
    n_eval_views: int
    n_gaussians: int
    scene_scale: float
    sh_degree: int
    minutes: float
    peak_vram_gb: float
    final_psnr: float
    final_ssim: float
    #: Quality of the model as *exported* — after the scale ceiling is applied.
    #: Equals ``final_psnr`` when the clamp is inert, which is the healthy case.
    export_psnr: float = 0.0
    export_ssim: float = 0.0
    scale_clamped_fraction: float = 0.0
    alive_fraction: float = 0.0
    #: Median max/min axis ratio over Gaussians that still render. The Luma
    #: reference capture sits near 12; runaway values mean needle splats that
    #: fit the training views and break between them.
    live_anisotropy_median: float = 0.0
    energy_kwh: float = 0.0
    cost_gbp: float = 0.0
    electricity_rate_gbp_per_kwh: float = 0.0
    history: list[dict] = field(default_factory=list)
    ply_path: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _write_progress(
    output_dir: Path,
    *,
    profile: Profile,
    step: int,
    n_gaussians: int,
    loss: float,
    l1_value: float,
    ssim_value: float,
    started: float,
    history: list["EvalResult"],
    energy_kwh: float = 0.0,
) -> None:
    """Live training state for the UI — ``train.json``'s in-progress cousin.

    ``train.json`` only exists once the run finishes, which left the
    dashboard with nothing to show for a run that's still going — this is
    what ``vitrine ui`` polls in the meantime. Deliberately cheap: overwrite
    one small file, no locking, tolerate readers racing a partial write
    (``serve.py`` already treats an unparseable JSON file as "no data").
    """
    elapsed_minutes = (time.time() - started) / 60.0
    steps_done = max(step, 1)
    eta_minutes = elapsed_minutes * (profile.iterations - step) / steps_done if step else None
    payload = {
        "profile": profile.name,
        "iterations": profile.iterations,
        "step": step,
        "n_gaussians": n_gaussians,
        "sh_degree": profile.sh_degree,
        "loss": round(loss, 4),
        "l1": round(l1_value, 4),
        "ssim": round(ssim_value, 4),
        "elapsed_minutes": round(elapsed_minutes, 1),
        "eta_minutes": round(eta_minutes, 1) if eta_minutes is not None else None,
        "energy_kwh": round(energy_kwh, 3),
        "cost_gbp": round(energy_kwh * electricity_rate_gbp_per_kwh(), 2),
        "history": [asdict(h) for h in history],
    }
    path = output_dir / "progress.json"
    try:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:  # a full disk here shouldn't abort training
        logger.debug("could not write progress.json: %s", exc)


def _knn_scale(points: np.ndarray, k: int = 4) -> np.ndarray:
    """Initial Gaussian radius from mean distance to k nearest neighbours.

    Sizing each splat to its local point density gives the optimiser a sane
    starting point; a constant would start every Gaussian either far too large
    (fog) or far too small (holes).
    """
    if len(points) == 0:
        return np.zeros((0,), dtype=np.float32)
    try:
        from scipy.spatial import KDTree

        tree = KDTree(points)
        distances, _ = tree.query(points, k=min(k, len(points)))
        mean_distance = distances[:, 1:].mean(axis=1) if distances.ndim > 1 and distances.shape[1] > 1 else np.full(len(points), 0.01)
    except ImportError:  # pragma: no cover - scipy is a hard dependency in practice
        logger.warning("scipy missing — falling back to a constant initial scale")
        mean_distance = np.full(len(points), 0.01)
    return np.clip(mean_distance, 1e-6, None).astype(np.float32)


def _initialise(model: Model, sh_degree: int, device: str) -> dict[str, torch.nn.Parameter]:
    """Seed Gaussians from the COLMAP sparse point cloud."""
    points = model.points_xyz
    colors = model.points_rgb
    if len(points) == 0:
        raise RuntimeError("COLMAP model contains no 3D points to initialise from")

    count = len(points)
    scales = np.log(_knn_scale(points))[:, None].repeat(3, axis=1)

    quats = np.zeros((count, 4), dtype=np.float32)
    quats[:, 0] = 1.0  # identity rotation, wxyz

    # sigmoid(2.0) ~= 0.88: start mostly opaque so early gradients are strong.
    opacities = np.full(count, 2.0, dtype=np.float32)

    sh0 = np.zeros((count, 1, 3), dtype=np.float32)
    sh0[:, 0, :] = (colors - 0.5) / SH_C0
    shN = np.zeros((count, sh_coefficient_count(sh_degree), 3), dtype=np.float32)

    def parameter(array: np.ndarray) -> torch.nn.Parameter:
        return torch.nn.Parameter(torch.from_numpy(np.ascontiguousarray(array)).float().to(device))

    logger.info("initialised %d Gaussians from the sparse cloud", count)
    return {
        "means": parameter(points),
        "scales": parameter(scales),
        "quats": parameter(quats),
        "opacities": parameter(opacities),
        "sh0": parameter(sh0),
        "shN": parameter(shN),
    }


def refine_every_guard(iterations: int) -> int:
    """Minimum width of the densification window, in steps.

    MCMC refines every 100 steps, so a window narrower than that would open and
    close without a single refinement ever firing.
    """
    return min(100, max(1, iterations // 4))


def _active_sh_degree(step: int, max_degree: int) -> int:
    """SH degree in use at ``step`` — one band introduced per interval.

    Fitting base colour before view-dependent terms avoids the higher bands
    absorbing error that really belongs to geometry.
    """
    return min(max_degree, step // SH_WARMUP_INTERVAL)


@torch.no_grad()
def evaluate(
    params: dict[str, torch.nn.Parameter],
    views: ViewSet,
    sh_degree: int,
    *,
    max_long_edge: int = 1600,
    limit: int | None = None,
) -> tuple[float, float]:
    """Mean PSNR and SSIM over held-out views, rendered whole."""
    from gsplat import rasterization

    # Stratified, not the first N: COLMAP image names sort by folder, so
    # `eval_indices[:8]` would be eight views from one camera and the reported
    # figure would describe that camera rather than the capture.
    indices = views.stratified_eval_indices(limit) if limit else views.eval_indices
    if not indices:
        return float("nan"), float("nan")

    psnr_values: list[float] = []
    ssim_values: list[float] = []
    sh = torch.cat([params["sh0"], params["shN"]], dim=1)

    for index in indices:
        batch = views.full(index, max_long_edge=max_long_edge)
        rendered, _, _ = rasterization(
            means=params["means"],
            quats=F.normalize(params["quats"], dim=-1),
            scales=torch.exp(params["scales"]),
            opacities=torch.sigmoid(params["opacities"]),
            colors=sh,
            viewmats=batch.world_to_camera,
            Ks=batch.intrinsics,
            width=batch.width,
            height=batch.height,
            sh_degree=sh_degree,
            packed=True,
            rasterize_mode="antialiased",
        )
        image = rendered[0].clamp(0.0, 1.0)
        psnr_values.append(psnr(image, batch.image))
        ssim_values.append(float(ssim(image, batch.image)))

    return float(np.mean(psnr_values)), float(np.mean(ssim_values))


def train(
    model: Model,
    images_dir: Path,
    output_dir: Path,
    profile: Profile,
    *,
    device: str = "cuda",
    eval_every: int = 2000,
    save_every: int = 10000,
    seed: int = 0,
    views: ViewSet | None = None,
) -> TrainReport:
    """Run training end to end and write ``scene.ply``.

    ``views`` accepts an already-built :class:`~vitrine.dataset.ViewSet`.
    Decoding and undistorting 700+ high-resolution views costs minutes and tens
    of gigabytes; a recipe sweep that varies only training knobs should pay
    that once, not once per candidate.
    """
    cuda_toolkit.configure()
    from gsplat import rasterization
    from gsplat.strategy import DefaultStrategy, MCMCStrategy

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(seed)
    generator = torch.Generator().manual_seed(seed)

    if views is None:
        views = ViewSet(
            model, images_dir,
            long_edge=profile.source_long_edge,
            device=device,
        )
    logger.info("view cache: %.2f GB in system RAM", views.memory_footprint_gb())

    params = _initialise(model, profile.sh_degree, device)
    scene_scale = views.scene_scale

    optimizers = {
        "means": torch.optim.Adam([params["means"]], lr=LR_MEANS_INIT * scene_scale, eps=1e-15),
        "scales": torch.optim.Adam([params["scales"]], lr=LR_SCALES, eps=1e-15),
        "quats": torch.optim.Adam([params["quats"]], lr=LR_QUATS, eps=1e-15),
        "opacities": torch.optim.Adam([params["opacities"]], lr=LR_OPACITIES, eps=1e-15),
        "sh0": torch.optim.Adam([params["sh0"]], lr=LR_SH0, eps=1e-15),
        "shN": torch.optim.Adam([params["shN"]], lr=LR_SH0 * LR_SHN_RATIO, eps=1e-15),
    }

    # Per-view exposure/white-balance, learned alongside the scene (see
    # APPEARANCE_OPT). Stored for every view so indexing is direct; only the
    # training views ever receive gradient.
    # Deliberately not in ``optimizers``: gsplat's strategies assert that dict
    # maps one-to-one onto the Gaussian parameters, and would try to
    # densify/relocate these alongside them.
    appearance_gain = torch.nn.Parameter(torch.ones(len(views), 3, device=device))
    appearance_bias = torch.nn.Parameter(torch.zeros(len(views), 3, device=device))
    appearance_optimizer = (
        torch.optim.Adam([appearance_gain, appearance_bias], lr=LR_APPEARANCE, eps=1e-15)
        if APPEARANCE_OPT
        else None
    )

    # Geometry must settle: decay the position LR by 100x over a fixed
    # horizon (see LR_DECAY_HORIZON_STEPS), not spread across the whole run.
    # Colour and opacity keep a constant rate, as in the reference.
    gamma = LR_MEANS_FINAL_RATIO ** (1.0 / LR_DECAY_HORIZON_STEPS)
    means_schedule = torch.optim.lr_scheduler.ExponentialLR(optimizers["means"], gamma=gamma)

    # Densification window, timed off the same horizon as the LR decay above
    # rather than profile.iterations directly. MCMC relocates/adds Gaussians
    # throughout this window, and a relocated Gaussian needs a meaningfully
    # high position LR afterwards to settle into place — closing the window
    # only once LR has already floored (which is what happens if this stays
    # tied to profile.iterations while the schedule above doesn't) means
    # newly relocated Gaussians get stranded wherever MCMC dropped them, with
    # no gradient signal left to correct it. Both ends still scale with a
    # short run so a fixed 500-step warm-up doesn't sit *after* the stop
    # point on anything under ~700 iterations.
    refine_horizon = min(profile.iterations, LR_DECAY_HORIZON_STEPS)
    refine_start = min(500, max(50, refine_horizon // 10))
    refine_stop = max(refine_start + refine_every_guard(refine_horizon),
                      int(refine_horizon * 0.75))

    n_colmap_points = len(model.points_xyz)
    scene_relative_cap = CAP_MAX_POINT_MULTIPLIER * n_colmap_points
    cap_max = min(profile.cap_max, max(scene_relative_cap, MIN_CAP_MAX))
    if cap_max < profile.cap_max:
        logger.info(
            "cap_max %s exceeds %dx this scene's %s COLMAP points; capping at %s instead",
            f"{profile.cap_max:,}", CAP_MAX_POINT_MULTIPLIER, f"{n_colmap_points:,}", f"{cap_max:,}",
        )

    if DENSIFICATION_STRATEGY == "default":
        strategy = DefaultStrategy(
            prune_opa=0.005,
            refine_start_iter=refine_start,
            refine_stop_iter=refine_stop,
            refine_every=100,
            verbose=False,
        )
        state = strategy.initialize_state(scene_scale=scene_scale)
        logger.info("default adaptive densification active over steps %d-%d", refine_start, refine_stop)
    elif DENSIFICATION_STRATEGY == "mcmc":
        strategy = MCMCStrategy(
            cap_max=cap_max,
            refine_start_iter=refine_start,
            refine_stop_iter=refine_stop,
            refine_every=100,
            min_opacity=0.005,
            verbose=False,
        )
        state = strategy.initialize_state()
        logger.info("MCMC densification active over steps %d-%d", refine_start, refine_stop)
    else:
        raise ValueError(f"unknown densification strategy: {DENSIFICATION_STRATEGY!r}")

    # Hold total regularisation pressure constant as the splat budget moves
    # (see OPACITY_REG_REFERENCE_CAP).
    reg_scale = OPACITY_REG_REFERENCE_CAP / max(cap_max, 1)
    opacity_reg = OPACITY_REG * reg_scale
    scale_reg = SCALE_REG * reg_scale if SCALE_REG_WITH_CAP else SCALE_REG
    if abs(reg_scale - 1.0) > 1e-6:
        logger.info(
            "cap %s is %.2fx the %s reference — opacity_reg %.4g -> %.4g, scale_reg %.4g -> %.4g",
            f"{cap_max:,}", 1 / reg_scale, f"{OPACITY_REG_REFERENCE_CAP:,}",
            OPACITY_REG, opacity_reg, SCALE_REG, scale_reg,
        )

    # How much of an average frame one crop covers. This is the single most
    # dangerous number in the recipe and it is not a knob anyone sets directly:
    # it falls out of `crop` and `source_long_edge` together.
    #
    # A Gaussian only receives reconstruction gradient on steps where it lands
    # inside the sampled window, but OPACITY_REG and SCALE_REG pull on every
    # Gaussian on every step. Coverage therefore *is* the ratio of signal to
    # regularisation, and below roughly half the frame the regularisers win and
    # the model collapses — opacity to near zero, scales to sub-renderable
    # spheres. Measured on this capture, 30k steps, everything else identical:
    #
    #   source  crop   coverage   PSNR    alive   median anisotropy
    #    1600   1024      64%    22.71     68%      50
    #    2304   1536      67%    22.82     70%      56
    #    3072   1536      45%    12.52      3%       2   <- collapsed
    #    2304   1024      35%    10.51      3%       2   <- collapsed
    #    3072   1024      11%    13.36     25%     100   <- collapsed
    #
    # Note that raising resolution while holding `crop` fixed silently lowers
    # coverage, which is why "train at a higher resolution" looks like it
    # breaks training. It does not — it starves it. Raise the crop with it.
    mean_view_pixels = float(np.mean([v.width * v.height for v in views.views]))
    coverage = min(1.0, (profile.crop**2) / mean_view_pixels)
    logger.info(
        "training %s: %d iters, cap %s, %dpx crops over ~%.0f px views (%.0f%% frame coverage), "
        "SH degree %d, scene_scale %.3f",
        profile.name, profile.iterations, f"{profile.cap_max:,}",
        profile.crop, mean_view_pixels, coverage * 100, profile.sh_degree, scene_scale,
    )
    if coverage < MIN_SAFE_COVERAGE:
        logger.warning(
            "frame coverage %.0f%% is below the %.0f%% that has held up in testing — expect the "
            "regularisers to outrun the reconstruction gradient. Raise crop to ~%d or lower "
            "source_long_edge.",
            coverage * 100, MIN_SAFE_COVERAGE * 100, int(math.sqrt(MIN_SAFE_COVERAGE * mean_view_pixels)),
        )
    logger.info("estimated wall clock: ~%.0f min", profile.estimated_minutes())

    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()

    background = torch.zeros(3, device=device)
    history: list[EvalResult] = []
    started = time.time()
    energy_kwh = 0.0
    last_power_sample_time = started

    for step in range(profile.iterations):
        sh_degree = _active_sh_degree(step, profile.sh_degree)

        batch = views.crop(views.sample_train_index(generator), profile.crop, generator=generator)

        colors = (
            params["sh0"]
            if sh_degree == 0
            else torch.cat([params["sh0"], params["shN"][:, : sh_coefficient_count(sh_degree)]], dim=1)
        )

        rendered, _, info = rasterization(
            means=params["means"],
            quats=F.normalize(params["quats"], dim=-1),
            scales=torch.exp(params["scales"]),
            opacities=torch.sigmoid(params["opacities"]),
            colors=colors,
            viewmats=batch.world_to_camera,
            Ks=batch.intrinsics,
            width=batch.width,
            height=batch.height,
            sh_degree=sh_degree,
            packed=True,
            rasterize_mode="antialiased",
            backgrounds=background,
            absgrad=False,
        )
        image = rendered[0]

        strategy.step_pre_backward(params, optimizers, state, step, info)

        if APPEARANCE_OPT:
            # This view's exposure, applied to the render rather than the
            # target: the scene keeps one canonical radiance field and the
            # camera's auto-exposure is modelled as what it is, a per-shot
            # transform of it.
            index = batch.view_index
            image = image * appearance_gain[index] + appearance_bias[index]

        loss, l1_value, ssim_value = photometric_loss(image, batch.image)
        # MCMC's own regularisers — without these the chain drifts toward many
        # large, faint Gaussians rather than a compact representation.
        if opacity_reg and not (OPACITY_REG_UNTIL_REFINE_STOP and step >= refine_stop):
            loss = loss + opacity_reg * torch.sigmoid(params["opacities"]).abs().mean()
        if scale_reg:
            loss = loss + scale_reg * torch.exp(params["scales"]).abs().mean()
        if APPEARANCE_OPT and APPEARANCE_REG:
            # Every view is free to rescale its own render, so brightness is a
            # gauge freedom: without an anchor the whole field can dim while
            # the gains drift up. Pull toward identity to fix it.
            loss = loss + APPEARANCE_REG * (
                (appearance_gain[index] - 1.0).pow(2).mean() + appearance_bias[index].pow(2).mean()
            )

        loss.backward()

        current_means_lr = optimizers["means"].param_groups[0]["lr"]
        with torch.no_grad():
            if DENSIFICATION_STRATEGY == "mcmc":
                strategy.step_post_backward(
                    params, optimizers, state, step, info, lr=current_means_lr
                )
            else:
                strategy.step_post_backward(
                    params, optimizers, state, step, info, packed=True
                )
            for optimizer in optimizers.values():
                optimizer.step()
                optimizer.zero_grad(set_to_none=True)
            if appearance_optimizer is not None:
                appearance_optimizer.step()
                appearance_optimizer.zero_grad(set_to_none=True)

            if MAX_ANISOTROPY is not None:
                # Bound the axis ratio by raising the *thin* axes to a floor
                # relative to the largest, rather than shrinking the large ones
                # toward the smallest.
                #
                # The direction matters and an earlier version had it the other
                # way round. Nothing in the objective bounds a Gaussian's
                # minimum thickness: flattening the axis normal to a surface
                # costs no photometric error and *earns* a SCALE_REG refund, so
                # over tens of thousands of Adam steps on log-scales it runs
                # away. Measured on this project's models, the median *live*
                # Gaussian has an axis ratio of 2,600 (nested-cinema-01) to
                # 541,000 (nested-cinema-03-hq); the Luma reference capture's is
                # 12. Those needles fit the training views and fall apart
                # between them.
                #
                # Anchoring to the smallest axis therefore shrinks a degenerate
                # splat to nothing — it takes the runaway value as truth and
                # destroys the footprint the photometric loss actually asked
                # for. Anchoring to the largest keeps the footprint and gives
                # the collapsed axis a floor, which is the shape the surface
                # needed in the first place.
                log_scales = params["scales"]
                log_max = log_scales.amax(dim=-1, keepdim=True)
                log_scales.copy_(
                    torch.maximum(log_scales, log_max - math.log(MAX_ANISOTROPY))
                )
        means_schedule.step()

        if step % 500 == 0:
            now = time.time()
            power = gpu_power_watts()
            if power is not None:
                energy_kwh += power * (now - last_power_sample_time) / 3_600_000
            last_power_sample_time = now

            with torch.no_grad():
                op_sigmoid = torch.sigmoid(params["opacities"])
                mean_opacity = float(op_sigmoid.mean())
                frac_alive = float((op_sigmoid > 0.005).float().mean())

            logger.info(
                "step %6d/%d  loss %.4f  L1 %.4f  SSIM %.4f  %s GS  sh%d  lr %.2e  "
                "mean_op %.4f  alive %.1f%%  %s",
                step, profile.iterations, float(loss.detach()), l1_value, ssim_value,
                f"{len(params['means']):,}", sh_degree, current_means_lr,
                mean_opacity, frac_alive * 100,
                f"{power:.0f} W" if power is not None else "power n/a",
            )
            _write_progress(
                output_dir, profile=profile, step=step, n_gaussians=len(params["means"]),
                loss=float(loss.detach()), l1_value=l1_value, ssim_value=ssim_value,
                started=started, history=history, energy_kwh=energy_kwh,
            )

        if eval_every and step > 0 and step % eval_every == 0:
            eval_psnr, eval_ssim = evaluate(params, views, profile.sh_degree, limit=8)
            result = EvalResult(step, eval_psnr, eval_ssim, len(params["means"]))
            history.append(result)
            logger.info("eval  %s", result.line())
            _write_progress(
                output_dir, profile=profile, step=step, n_gaussians=len(params["means"]),
                loss=float(loss.detach()), l1_value=l1_value, ssim_value=ssim_value,
                started=started, history=history, energy_kwh=energy_kwh,
            )

        if save_every and step > 0 and step % save_every == 0:
            _write(params, output_dir / f"checkpoint_{step}.ply", profile.sh_degree, scene_scale)

    minutes = (time.time() - started) / 60.0
    final_psnr, final_ssim = evaluate(params, views, profile.sh_degree)
    logger.info("final eval over %d held-out views: PSNR %.2f dB, SSIM %.4f",
                len(views.eval_indices), final_psnr, final_ssim)

    # Export applies a hard scale ceiling, so the file that ships is not
    # necessarily the model that was just scored. On a run where scales have
    # run away that gap has measured 2.6 dB — worth knowing before the PLY is
    # treated as the result. Score the clamped model too, and report both.
    with torch.no_grad():
        ceiling = math.log(MAX_SCALE_FRACTION * scene_scale)
        clamped_fraction = float((params["scales"] > ceiling).any(dim=1).float().mean())
        op_sigmoid = torch.sigmoid(params["opacities"])
        alive = op_sigmoid > 0.005
        alive_fraction = float(alive.float().mean())
        # Shape health of the population that actually renders. Dead Gaussians
        # are unconstrained, so averaging them in measures debris.
        log_scales = params["scales"][alive]
        if len(log_scales):
            ratios = torch.exp(log_scales.amax(dim=-1) - log_scales.amin(dim=-1))
            live_anisotropy_median = float(ratios.median())
        else:
            live_anisotropy_median = float("nan")
    if clamped_fraction > 0.001:
        export_params = dict(params)
        export_params["scales"] = torch.nn.Parameter(params["scales"].clamp(max=ceiling))
        export_psnr, export_ssim = evaluate(export_params, views, profile.sh_degree)
        logger.info(
            "export clamp touches %.1f%% of Gaussians: %.2f dB / %.4f after clamping (%.2f dB cost)",
            clamped_fraction * 100, export_psnr, export_ssim, final_psnr - export_psnr,
        )
        if EXPORT_CLAMP_STUDY:
            # The ceiling is a judgement call about how much a handful of very
            # large Gaussians are allowed to fog a viewer, and it is charged
            # against a metric nobody was measuring. Price it.
            for fraction in EXPORT_CLAMP_STUDY:
                trial = dict(params)
                trial["scales"] = torch.nn.Parameter(
                    params["scales"].clamp(max=math.log(fraction * scene_scale))
                )
                trial_psnr, trial_ssim = evaluate(trial, views, profile.sh_degree, limit=16)
                touched = float(
                    (params["scales"] > math.log(fraction * scene_scale)).any(dim=1).float().mean()
                )
                logger.info(
                    "  clamp study: max_scale_fraction %.3f -> %.2f dB / %.4f (%.2f%% touched)",
                    fraction, trial_psnr, trial_ssim, touched * 100,
                )
    else:
        export_psnr, export_ssim = final_psnr, final_ssim
    logger.info(
        "alive Gaussians at finish: %.1f%%, median live anisotropy %.1f",
        alive_fraction * 100, live_anisotropy_median,
    )

    ply_path = _write(params, output_dir / "scene.ply", profile.sh_degree, scene_scale)

    rate = electricity_rate_gbp_per_kwh()
    logger.info("energy: %.3f kWh (~£%.2f at £%.4f/kWh)", energy_kwh, energy_kwh * rate, rate)

    peak_vram = torch.cuda.max_memory_allocated() / 2**30 if device.startswith("cuda") else 0.0
    report = TrainReport(
        profile=profile.name,
        iterations=profile.iterations,
        n_train_views=len(views.train_indices),
        n_eval_views=len(views.eval_indices),
        n_gaussians=len(params["means"]),
        scene_scale=scene_scale,
        sh_degree=profile.sh_degree,
        minutes=round(minutes, 1),
        peak_vram_gb=round(peak_vram, 2),
        final_psnr=round(final_psnr, 3),
        final_ssim=round(final_ssim, 4),
        export_psnr=round(export_psnr, 3),
        export_ssim=round(export_ssim, 4),
        scale_clamped_fraction=round(clamped_fraction, 4),
        alive_fraction=round(alive_fraction, 4),
        live_anisotropy_median=round(live_anisotropy_median, 2),
        energy_kwh=round(energy_kwh, 3),
        cost_gbp=round(energy_kwh * rate, 2),
        electricity_rate_gbp_per_kwh=rate,
        history=[asdict(h) for h in history],
        ply_path=str(ply_path),
    )
    (output_dir / "train.json").write_text(report.to_json(), encoding="utf-8")
    (output_dir / "progress.json").unlink(missing_ok=True)  # train.json is authoritative now
    logger.info("training complete in %.1f min — %s", minutes, ply_path)
    return report


def _write(
    params: dict[str, torch.nn.Parameter],
    path: Path,
    sh_degree: int,
    scene_scale: float,
) -> Path:
    detached = {k: v.detach().cpu().numpy() for k, v in params.items()}
    return write_splat_ply(
        path,
        means=detached["means"],
        scales=detached["scales"],
        quats=detached["quats"],
        opacities=detached["opacities"],
        sh0=detached["sh0"],
        shN=detached["shN"],
        sh_degree=sh_degree,
        max_scale=MAX_SCALE_FRACTION * scene_scale,
    )
