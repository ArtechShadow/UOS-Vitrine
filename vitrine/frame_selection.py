"""Deterministic, bounded frame selection for video ingest.

The legacy ingest path selected frames by Laplacian sharpness only.  That
remains available through :func:`vitrine.ingest.select_sharpest` for runs that
pass an explicit ``video_budget``.  The adaptive path in this module adds
simple, local image signals that are cheap to compute and easy to report:

* sharpness (variance of the Laplacian),
* exposure and highlight/shadow clipping,
* near-duplicate distance to the preceding frame, and
* temporal buckets so the chosen views cover the complete clip.

No machine-learning package is required.  OpenCV is imported lazily, matching
the existing ingest sharpness helper.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import numpy as np

# This is a guard on *derived* video frames, not a promise about the number of
# views selected for a reconstruction.  A long clip lowers extraction FPS to
# stay inside the guard while retaining its first and last timestamps.
GLOBAL_FRAME_SAFETY_CAP = 2400


@dataclass(frozen=True)
class SelectionPreset:
    """Parameters for adaptive video selection.

    ``target_fps`` determines the desired number of views from the probed clip
    duration.  The min/max range keeps short clips useful and prevents a very
    long clip from turning an ingest into an unbounded job.
    """

    name: str
    target_fps: float
    min_frames: int
    max_frames: int
    exposure_floor: float = 0.08
    clipping_limit: float = 0.55
    duplicate_delta: float = 0.018


SELECTION_PRESETS: dict[str, SelectionPreset] = {
    # Fast enough for a venue rehearsal while preserving both ends of a clip.
    "fast-demo": SelectionPreset("fast-demo", target_fps=1.5, min_frames=48, max_frames=180),
    # Balanced is the default for normal local ingest.
    "balanced": SelectionPreset("balanced", target_fps=2.5, min_frames=96, max_frames=360),
    # More views for an archival run.  This remains bounded and does not alter
    # the explicit integer-budget compatibility path.
    "archive": SelectionPreset("archive", target_fps=4.0, min_frames=160, max_frames=900),
}


@dataclass
class FrameScore:
    """Signals measured for one candidate frame."""

    path: Path
    index: int
    sharpness: float
    exposure: float
    clipping: float
    duplicate_delta: float | None
    composite: float


@dataclass
class SelectionResult:
    """Selection output plus counters suitable for an ingest report."""

    kept: list[Path]
    rejected: list[tuple[Path, float, str]]
    scores: list[FrameScore] = field(default_factory=list)
    budget: int = 0
    counters: dict[str, int] = field(default_factory=dict)


def resolve_selection_preset(name: str | SelectionPreset | None) -> SelectionPreset:
    """Resolve a preset name and fail early for typos in CLI/config input."""

    if isinstance(name, SelectionPreset):
        return name
    value = "balanced" if name is None else str(name).strip().lower()
    try:
        return SELECTION_PRESETS[value]
    except KeyError as exc:
        valid = ", ".join(SELECTION_PRESETS)
        raise ValueError(f"unknown selection_preset {name!r}; choose one of {valid}") from exc


def validate_selection_params(
    selection_preset: str | SelectionPreset | None,
    video_budget: int | None,
) -> SelectionPreset:
    """Validate public ingest selection parameters and return the preset."""

    preset = resolve_selection_preset(selection_preset)
    if video_budget is not None:
        # bool is an int subclass, but accepting True here makes a typo look
        # like a one-frame ingest and is especially surprising from argparse.
        if isinstance(video_budget, bool) or not isinstance(video_budget, int) or video_budget <= 0:
            raise ValueError("video_budget must be a positive integer or None")
        if video_budget > GLOBAL_FRAME_SAFETY_CAP:
            raise ValueError(
                f"video_budget {video_budget} exceeds the global frame safety cap "
                f"({GLOBAL_FRAME_SAFETY_CAP}); use adaptive archive selection for a bounded larger run"
            )
    return preset


def adaptive_budget(
    candidate_count: int,
    duration_seconds: float | None,
    preset: SelectionPreset,
) -> int:
    """Return a deterministic bounded budget for one extracted video group."""

    if candidate_count <= 0:
        return 0
    if duration_seconds is not None and np.isfinite(duration_seconds) and duration_seconds > 0:
        desired = round(float(duration_seconds) * preset.target_fps)
    else:
        # If a demuxer cannot expose duration, retain a conservative fraction
        # of the observed candidates rather than pretending the clip is short.
        desired = round(candidate_count * 0.6)
    desired = max(preset.min_frames, desired)
    desired = min(preset.max_frames, desired, GLOBAL_FRAME_SAFETY_CAP)
    return max(1, min(candidate_count, desired))


def _gray(path: Path, working_long_edge: int = 800) -> np.ndarray | None:
    """Read a small normalized grayscale image for cheap comparable metrics."""

    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    height, width = image.shape
    long_edge = max(height, width)
    if long_edge > working_long_edge:
        scale = working_long_edge / long_edge
        image = cv2.resize(
            image,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    # Resize again to make duplicate comparisons insensitive to camera
    # resolution while retaining enough room structure to detect repeats.
    image = cv2.resize(image, (64, 64), interpolation=cv2.INTER_AREA)
    return image.astype(np.float32) / 255.0


def _exposure_metrics(gray: np.ndarray | None) -> tuple[float, float]:
    """Return (quality, clipping_fraction) in the range 0..1."""

    if gray is None or gray.size == 0:
        return 0.0, 1.0
    low = float((gray <= 0.02).mean())
    high = float((gray >= 0.98).mean())
    clipping = min(1.0, low + high)
    mean = float(gray.mean())
    # Installation footage is allowed to be dark; the floor only rejects
    # frames that contain almost no usable signal.  Clipped pixels are a
    # separate penalty because a sensible mean can hide a blown window.
    mid_quality = max(0.0, 1.0 - abs(mean - 0.42) / 0.42)
    clip_quality = max(0.0, 1.0 - clipping / 0.30)
    return max(0.0, min(1.0, 0.72 * mid_quality + 0.28 * clip_quality)), clipping


def _sharpness(
    path: Path,
    gray: np.ndarray | None,
    sharpness_fn: Callable[[Path], float] | None,
) -> float:
    if sharpness_fn is not None:
        try:
            return float(max(0.0, sharpness_fn(path)))
        except (OSError, ValueError, TypeError):
            return 0.0
    if gray is None:
        return 0.0
    import cv2

    return float(max(0.0, cv2.Laplacian((gray * 255).astype(np.uint8), cv2.CV_64F).var()))


def _normalise(values: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(values), dtype=np.float64)
    finite = np.isfinite(values)
    if not finite.any():
        return np.zeros_like(values)
    lo = float(values[finite].min())
    hi = float(values[finite].max())
    if hi <= lo:
        return np.full_like(values, 0.5)
    values = np.nan_to_num(values, nan=lo, posinf=hi, neginf=lo)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def select_adaptive(
    group,
    preset: str | SelectionPreset = "balanced",
    *,
    budget: int | None = None,
    duration_seconds: float | None = None,
    sharpness_fn: Callable[[Path], float] | None = None,
    on_scored=None,
) -> SelectionResult:
    """Select frames by quality, continuity, and duplicate suppression.

    The input order is treated as temporal order (ffmpeg frame names and
    sorted stills are deterministic).  Every temporal bucket contributes at
    most one frame, so a bright/sharp section cannot consume the whole budget.
    ``on_scored`` retains the existing ``(path, score)`` callback shape.
    """

    resolved = resolve_selection_preset(preset)
    paths = list(group.paths)
    if budget is None:
        budget = adaptive_budget(len(paths), duration_seconds, resolved)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("adaptive selection budget must be a positive integer")
    budget = min(budget, GLOBAL_FRAME_SAFETY_CAP)
    if not paths:
        return SelectionResult([], [], budget=0, counters={"scored": 0, "kept": 0, "rejected": 0})

    measurements: list[tuple[Path, np.ndarray | None, float, float, float, float | None]] = []
    previous: np.ndarray | None = None
    for index, path in enumerate(paths):
        gray = _gray(path)
        quality, clipping = _exposure_metrics(gray)
        sharp = _sharpness(path, gray, sharpness_fn)
        delta = None if previous is None or gray is None else float(np.abs(gray - previous).mean())
        previous = gray if gray is not None else previous
        measurements.append((path, gray, sharp, quality, clipping, delta))

    sharpness_values = _normalise(item[2] for item in measurements)
    deltas = np.asarray([item[5] if item[5] is not None else 1.0 for item in measurements], dtype=np.float64)
    # A low frame-to-frame delta is a duplicate signal.  For scoring, keep a
    # gentle penalty; the hard decision below removes only consecutive repeats.
    novelty = np.clip(deltas / max(resolved.duplicate_delta * 4.0, 1e-6), 0.0, 1.0)
    scores: list[FrameScore] = []
    for index, (path, _gray_image, sharp, exposure, clipping, delta) in enumerate(measurements):
        composite = float(0.58 * sharpness_values[index] + 0.32 * exposure + 0.10 * novelty[index])
        score = FrameScore(path, index, sharp, exposure, clipping, delta, composite)
        scores.append(score)
        if on_scored:
            on_scored(path, composite)

    rejected: list[tuple[Path, float, str]] = []
    candidates: list[FrameScore] = []
    exposure_rejected = 0
    duplicate_rejected = 0
    for score in scores:
        if score.exposure < resolved.exposure_floor or score.clipping > resolved.clipping_limit:
            exposure_rejected += 1
            rejected.append((score.path, score.composite, "unusable exposure or severe clipping"))
            continue
        if score.duplicate_delta is not None and score.duplicate_delta <= resolved.duplicate_delta:
            duplicate_rejected += 1
            rejected.append((score.path, score.composite, "near-duplicate of preceding frame"))
            continue
        candidates.append(score)

    # Never let hard filters erase a tiny clip.  Retaining the best frame is
    # more useful to the caller than producing an empty camera group.
    if not candidates and scores:
        best = max(scores, key=lambda item: item.composite)
        candidates = [best]
        rejected = [(p, s, r) for p, s, r in rejected if p is not best.path]
        exposure_rejected = max(0, exposure_rejected - 1)
        duplicate_rejected = max(0, duplicate_rejected - 1)

    kept_scores: list[FrameScore]
    bucket_rejected = 0
    if len(candidates) <= budget:
        kept_scores = candidates
    else:
        kept_scores = []
        edges = np.linspace(0, len(candidates), budget + 1).astype(int)
        for start, end in pairwise(edges):
            bucket = candidates[start:end]
            if not bucket:
                continue
            best = max(bucket, key=lambda item: item.composite)
            kept_scores.append(best)
            for score in bucket:
                if score.path is not best.path:
                    bucket_rejected += 1
                    rejected.append((score.path, score.composite, "not highest quality in temporal coverage bucket"))

    kept = [score.path for score in kept_scores]
    counters = {
        "scored": len(scores),
        "kept": len(kept),
        "rejected": len(rejected),
        "exposure_rejected": exposure_rejected,
        "duplicate_rejected": duplicate_rejected,
        "coverage_bucket_rejected": bucket_rejected,
        "budget": budget,
    }
    return SelectionResult(kept, rejected, scores=scores, budget=budget, counters=counters)
