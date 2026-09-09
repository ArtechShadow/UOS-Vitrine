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
measure — it understated this card by roughly 4-5x.  The legacy workstation
estimate remains in the table for reproducibility, but it is tied to that
benchmark family and is never a promise for another GPU model.

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
from collections.abc import Mapping
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
    #: Only used to scale legacy time estimates; nothing else depends on it.
    #: ``None`` means this profile has no transferable throughput estimate.
    relative_throughput: float | None = 1.0
    #: Hardware family for the benchmark behind an estimate.  A caller that
    #: supplies a hardware snapshot gets no ETA when the model does not match.
    benchmark_hardware: str | None = None
    #: A measured wall-clock value, when one exists for this exact recipe.
    #: This is deliberately separate from ``relative_throughput``: no ratio is
    #: inferred for a different machine.
    measured_runtime_minutes: float | None = None
    #: Source record for an exact measured runtime or a legacy benchmark.
    estimate_source: str | None = None

    @property
    def nominal_coverage(self) -> float:
        """The crop/source ratio used for the frame-coverage safety rule."""
        if self.source_long_edge <= 0:
            return 0.0
        return min(1.0, self.crop / self.source_long_edge)

    def validation_warnings(self) -> tuple[str, ...]:
        """Warnings that can be shown without running a capture.

        The legacy workstation archive values are intentionally preserved for
        historical comparison.  Its 1600/4096 crop/source ratio is below the
        50% rule measured for this pipeline, so callers must surface that fact
        rather than treating the row as a validated recipe.
        """
        if self.nominal_coverage < 0.5:
            warning = (
                f"nominal frame coverage is {self.nominal_coverage:.0%}, below the 50% "
                "safe-coverage rule; this profile is unvalidated for general captures"
            )
            return (warning,)
        return ()

    @staticmethod
    def _hardware_model(hardware: Mapping[str, Any] | str | None) -> str | None:
        if hardware is None:
            return None
        if isinstance(hardware, str):
            return hardware.strip() or None
        if not isinstance(hardware, Mapping):
            return None
        # Accept both the flat detect_hardware() record and the nested runtime
        # record so callers do not have to reshape the public API response.
        model = hardware.get("gpu_model")
        if model is None and isinstance(hardware.get("hardware"), Mapping):
            model = hardware["hardware"].get("gpu_model")
        return str(model).strip() if model else None

    def _matches_benchmark(self, hardware: Mapping[str, Any] | str | None) -> bool:
        if not self.benchmark_hardware:
            return True
        model = self._hardware_model(hardware)
        if not model:
            return False
        expected = self.benchmark_hardware.casefold()
        actual = model.casefold()
        # The measured label is deliberately a family token.  This matches
        # "NVIDIA GeForce RTX 5090" while still rejecting an A6000.
        return expected in actual or actual in expected

    def estimated_minutes(
        self,
        visible_fraction: float = 0.39,
        *,
        hardware: Mapping[str, Any] | str | None = None,
    ) -> float | None:
        """Rough wall-clock estimate from the measured scaling above.

        Interpolates the 768x768 laptop measurements linearly in splat count
        and quadratically in crop area, then divides by the tier's throughput.
        This is an historical estimate for the legacy rows.  If a hardware
        snapshot is supplied, its GPU model must match the benchmark family.
        The exact ``demo`` recipe returns its one measured value only for the
        RTX 5090 family; it returns ``None`` for an unknown GPU or another
        model rather than inventing a transfer estimate.
        """
        if self.measured_runtime_minutes is not None:
            return self.measured_runtime_minutes if self._matches_benchmark(hardware) else None
        if self.relative_throughput is None:
            return None
        if hardware is not None and not self._matches_benchmark(hardware):
            return None
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
    benchmark_hardware="RTX 3060 Laptop",
    estimate_source="profiles.py legacy 3060 benchmark",
)

LAPTOP_STANDARD = Profile(
    name="laptop-standard",
    source_long_edge=2048,
    crop=768,
    cap_max=1_000_000,
    iterations=15_000,
    sh_degree=3,
    colmap_long_edge=2000,
    benchmark_hardware="RTX 3060 Laptop",
    estimate_source="profiles.py legacy 3060 benchmark",
)

LAPTOP_ARCHIVE = Profile(
    name="laptop-archive",
    source_long_edge=2560,
    crop=768,
    cap_max=1_500_000,
    iterations=30_000,
    sh_degree=3,
    colmap_long_edge=2400,
    benchmark_hardware="RTX 3060 Laptop",
    estimate_source="profiles.py legacy 3060 benchmark",
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
    benchmark_hardware="RTX 5090",
    estimate_source="profiles.py synthetic 5090 benchmark rows",
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
    benchmark_hardware="RTX 5090",
    estimate_source="profiles.py synthetic 5090 benchmark rows",
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
    benchmark_hardware="RTX 5090",
    estimate_source="profiles.py synthetic 5090 benchmark rows",
)

# --- Demo: measured real-capture recipe -----------------------------------
# This is the exact recipe recorded in docs/demo-video-quality-20260906.md.
# The 4.3-minute value is a single RTX 5090 video-only training run after the
# real data's sparse-point guard reduced the effective cap.  It is therefore a
# measured reference for that GPU family, not a general workstation ETA.

DEMO = Profile(
    name="demo",
    source_long_edge=2304,
    crop=1536,
    cap_max=2_000_000,
    iterations=15_000,
    sh_degree=3,
    colmap_long_edge=3200,
    relative_throughput=None,
    benchmark_hardware="RTX 5090",
    measured_runtime_minutes=4.3,
    estimate_source="docs/demo-video-quality-20260906.md",
)

_TABLE: dict[str, dict[str, Profile]] = {
    "laptop": {
        "draft": LAPTOP_DRAFT,
        "standard": LAPTOP_STANDARD,
        "archive": LAPTOP_ARCHIVE,
        "demo": DEMO,
    },
    "workstation": {
        "draft": WORKSTATION_DRAFT,
        "standard": WORKSTATION_STANDARD,
        "archive": WORKSTATION_ARCHIVE,
        "demo": DEMO,
    },
}

QUALITY_LEVELS = ("draft", "standard", "archive", "demo")
TIERS = ("laptop", "workstation")


def detect_tier() -> str:
    """Pick a tier from the attached GPU. Falls back to ``laptop`` when unsure.

    Erring toward ``laptop`` is deliberate: an under-provisioned run is slow but
    completes, whereas an over-provisioned one dies partway through.
    """
    try:
        from .hardware import detect_hardware

        snapshot = detect_hardware()
        # Keep this old two-tier API as a compatibility bridge.  The generic
        # capability_tier in hardware.py is authoritative for new callers.
        return "workstation" if snapshot.get("capability_tier") == "high" else "laptop"
    except (ImportError, OSError, RuntimeError):
        # A broken optional probe should not prevent the profile table from
        # being inspected.  Preserve the historical torch fallback.
        try:
            import torch
        except ImportError:
            logger.warning("hardware probe unavailable — assuming laptop tier")
            return "laptop"
        try:
            if not torch.cuda.is_available():
                logger.warning("no CUDA device — assuming laptop tier")
                return "laptop"
            vram_gb = torch.cuda.get_device_properties(0).total_memory / 2**30
            return "workstation" if vram_gb >= 20 else "laptop"
        except Exception:  # noqa: BLE001 - optional diagnostic probe
            logger.warning("CUDA probe failed — assuming laptop tier")
            return "laptop"


def resolve(quality: str = "archive", tier: str | None = None) -> Profile:
    """Look up a profile by quality and tier, detecting the tier if omitted."""
    quality = (quality or "archive").lower()
    if quality not in QUALITY_LEVELS:
        raise ValueError(f"quality must be one of {QUALITY_LEVELS}, got {quality!r}")

    tier = (tier or detect_tier()).lower()
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")

    return _TABLE[tier][quality]


def describe(
    profile: Profile,
    *,
    hardware: Mapping[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Profile as a plain dict plus its time estimate, for manifests and logs."""
    out = asdict(profile)
    estimate = profile.estimated_minutes(hardware=hardware)
    out["estimated_minutes"] = round(estimate, 1) if estimate is not None else None
    out["nominal_coverage"] = round(profile.nominal_coverage, 4)
    out["warnings"] = list(profile.validation_warnings())
    return out
