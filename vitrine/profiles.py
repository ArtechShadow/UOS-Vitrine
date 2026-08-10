"""Hardware profiles — the knobs that change between a laptop and a lab GPU.

Every number here was measured on the target hardware rather than guessed, and
the measurements overturned the assumption the project started with.

Benchmark, RTX 3060 Laptop (6 GB), 1.5M splats, SH degree 3, 768x768 render,
full forward + backward including the photometric loss:

    peak VRAM                  1.46 GB      <- not 5+ GB
    all splats in frustum       500 ms/iter
    ~39% in frustum             222 ms/iter
    ~15% in frustum              95 ms/iter

Two conclusions follow, and they shape this whole module:

1. **VRAM is not the binding constraint on the laptop — time is.** At the
   settings we care about, a 6 GB card uses about a quarter of its memory. The
   splat cap is therefore a *throughput* control, not a memory control.

2. **Cost scales with splats projecting into the view**, not with the total
   count. A camera inside a room sees perhaps a third of the scene at once, so
   room interiors land near the middle row above.

Adding SSIM to the loss costs 9-17% — cheap enough that the reference 3DGS
objective is simply always on. See ``losses`` for why it is not ``fused-ssim``.

The render window is a random crop of a larger source image (see ``train``).
That keeps full-resolution detail available to the optimiser while bounding
per-step cost, which is what makes readable fine detail affordable on a 6 GB
laptop.

Benchmark, RTX 5090 (32 GB), same 1.5M splats / SH degree 3 / 768x768 / full
forward+backward+loss methodology, synthetic scene, camera distance tuned per
row (see ``scripts`` history — not checked in; re-run against a real scene if
these numbers ever need re-deriving):

    ~100% in frustum             14.4 ms/iter   (vs 500 ms on the 3060: ~35x)
    ~79% in frustum              13.9 ms/iter   (vs 222 ms at ~39%: ~32x, not
                                                  a clean comparison — see below)
    ~46% in frustum              12.2 ms/iter   (vs 95 ms at ~15%: ~21x)

The previous ``relative_throughput=7.0`` for the workstation tier was a
spec-sheet guess made before any 4090/5090-class card was available to
measure — it understated this card by roughly 4-5x.

The three 5090 rows above are far flatter than the 3060's (14.4 vs 12.2 ms,
a 1.2x spread, against the 3060's 500 vs 95 ms, a 5x spread) despite covering
a similar range of visible fraction. That is not noise: at 12-14 ms/iter,
Python-loop and kernel-launch overhead — which does not shrink with the
GPU — is a much larger fraction of the step than it was at 95-500 ms on the
3060, so the "cost scales with visible fraction" model this module encodes is
already an approximation for this card, not a law. ``relative_throughput``
below is calibrated against the ~100%-visible row (the cleanest of the three
to reproduce exactly) as ``500 / 14.4 ≈ 35``, rounded down to 32 to stay on
the conservative side given that flattening.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Profile:
    """Resolved settings for one machine and one quality preset."""

    name: str
    #: Longest edge the source images are held at, on CPU. The optimiser can
    #: only ever recover detail that survives this resize.
    source_long_edge: int
    #: Side of the square random crop rendered each step. Drives per-step cost.
    crop: int
    #: MCMC hard ceiling on splat count. Primarily a speed control here.
    cap_max: int
    iterations: int
    sh_degree: int
    #: Longest edge fed to COLMAP feature extraction and undistortion.
    colmap_long_edge: int
    #: Throughput relative to the RTX 3060 Laptop the benchmarks were run on.
    #: Only used to scale time estimates; nothing else depends on it.
    relative_throughput: float = 1.0

    def estimated_minutes(self, visible_fraction: float = 0.39) -> float:
        """Rough wall-clock estimate from the measured scaling above.

        Interpolates the 768x768 laptop measurements linearly in splat count
        and quadratically in crop area, then divides by the tier's throughput.
        Indicative only: real scenes vary with depth complexity and overlap,
        and the throughput factor for anything other than the 3060 is a
        specification-sheet estimate rather than a measurement.
        """
        ms_per_iter = 222.0 * (self.cap_max / 1_500_000) * (self.crop / 768) ** 2
        ms_per_iter *= visible_fraction / 0.39
        ms_per_iter /= max(self.relative_throughput, 1e-6)
        return ms_per_iter * self.iterations / 60_000


# --- Laptop: RTX 3060 Laptop 6 GB -------------------------------------------
# Compute-bound. The cap and iteration count are chosen so an archive run
# finishes in roughly an hour rather than to fit memory.

LAPTOP_DRAFT = Profile(
    name="laptop-draft",
    source_long_edge=1600,
    crop=512,
    cap_max=400_000,
    iterations=7_000,
    sh_degree=2,
    colmap_long_edge=1600,
)

LAPTOP_STANDARD = Profile(
    name="laptop-standard",
    source_long_edge=2048,
    crop=768,
    cap_max=1_000_000,
    iterations=15_000,
    sh_degree=3,
    colmap_long_edge=2000,
)

LAPTOP_ARCHIVE = Profile(
    name="laptop-archive",
    source_long_edge=2560,
    crop=768,
    cap_max=1_500_000,
    iterations=30_000,
    sh_degree=3,
    colmap_long_edge=2400,
)

# --- Workstation: RTX 4090 / 5090 class, 24-32 GB ---------------------------
# relative_throughput=32.0 is measured on an RTX 5090 (see module docstring),
# not a spec-sheet guess — supersedes the earlier relative_throughput=7.0.
# Crop/cap were sized for the earlier throughput estimate and not revisited
# here: at the corrected number these finish in a fraction of their old
# estimated_minutes(), which is headroom to raise cap_max/crop for a real
# quality push rather than a reason to shrink the profile.
#
# cap_max below is a *ceiling*, not a target: train.train() clamps it to
# CAP_MAX_POINT_MULTIPLIER x the actual COLMAP point count for the scene
# being trained, because MCMC fills whatever cap it's given regardless of
# whether the scene supports that many useful Gaussians. Measured on a
# single-room, 272-image, ~112K-point capture: workstation-archive's
# cap_max=6,000,000 (~54x the point count) collapsed 98.7% of the final
# Gaussians to ~zero opacity and finished at 14.3 dB PSNR; the clamp brought
# the effective cap down to ~1.68M and produces a properly converged result
# instead. See train.py for the full measurement.

WORKSTATION_DRAFT = Profile(
    name="workstation-draft",
    source_long_edge=2048,
    crop=800,
    cap_max=1_000_000,
    iterations=7_000,
    sh_degree=2,
    colmap_long_edge=2000,
    relative_throughput=32.0,
)

WORKSTATION_STANDARD = Profile(
    name="workstation-standard",
    source_long_edge=3200,
    crop=1280,
    cap_max=3_000_000,
    iterations=20_000,
    sh_degree=3,
    colmap_long_edge=3200,
    relative_throughput=32.0,
)

WORKSTATION_ARCHIVE = Profile(
    name="workstation-archive",
    source_long_edge=4096,
    crop=1600,
    cap_max=6_000_000,
    iterations=30_000,
    sh_degree=3,
    colmap_long_edge=3840,
    relative_throughput=32.0,
)

_TABLE: dict[str, dict[str, Profile]] = {
    "laptop": {
        "draft": LAPTOP_DRAFT,
        "standard": LAPTOP_STANDARD,
        "archive": LAPTOP_ARCHIVE,
    },
    "workstation": {
        "draft": WORKSTATION_DRAFT,
        "standard": WORKSTATION_STANDARD,
        "archive": WORKSTATION_ARCHIVE,
    },
}

QUALITY_LEVELS = ("draft", "standard", "archive")
TIERS = ("laptop", "workstation")


def detect_tier() -> str:
    """Pick a tier from the attached GPU. Falls back to ``laptop`` when unsure.

    Erring toward ``laptop`` is deliberate: an under-provisioned run is slow but
    completes, whereas an over-provisioned one dies partway through.
    """
    try:
        import torch
    except ImportError:
        logger.warning("torch unavailable — assuming laptop tier")
        return "laptop"
    if not torch.cuda.is_available():
        logger.warning("no CUDA device — assuming laptop tier")
        return "laptop"

    vram_gb = torch.cuda.get_device_properties(0).total_memory / 2**30
    return "workstation" if vram_gb >= 20 else "laptop"


def resolve(quality: str = "archive", tier: str | None = None) -> Profile:
    """Look up a profile by quality and tier, detecting the tier if omitted."""
    quality = (quality or "archive").lower()
    if quality not in QUALITY_LEVELS:
        raise ValueError(f"quality must be one of {QUALITY_LEVELS}, got {quality!r}")

    tier = (tier or detect_tier()).lower()
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")

    return _TABLE[tier][quality]


def describe(profile: Profile) -> dict[str, Any]:
    """Profile as a plain dict plus its time estimate, for manifests and logs."""
    out = asdict(profile)
    out["estimated_minutes"] = round(profile.estimated_minutes(), 1)
    return out
