"""Turn a folder of source media into COLMAP-ready image groups.

Three jobs, in order:

1. **Classify** the source into *camera groups*. A group is one physical
   camera at one resolution — 25 MP iPhone stills are one group, 720p video
   frames are another. Each group becomes its own output folder, because
   ``sfm`` asks COLMAP for one camera model **per folder**. Getting this wrong
   does not raise an error; it silently fits one set of intrinsics to two
   different lenses and warps the reconstruction.

2. **Extract** video frames at a sensible rate, oversampling so step 3 has
   something to choose between.

3. **Select** by sharpness. The subject here is a dimly-lit installation, so
   handheld footage carries real motion blur. Frames are scored by the
   variance of the Laplacian and the sharpest frame in each temporal bucket
   wins, which keeps coverage even along the camera path instead of letting
   one well-lit stretch dominate.

EXIF survives every resize. COLMAP reads the focal length from EXIF to seed
its intrinsics, and throwing that away makes the solver guess from scratch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from .frame_selection import (
    GLOBAL_FRAME_SAFETY_CAP,
    SelectionPreset,
    select_adaptive,
    validate_selection_params,
)

logger = logging.getLogger(__name__)

# HEIC/HEIF (iPhone stills) need pillow-heif registered once per process.
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    _HEIF_OK = True
except ImportError:  # pragma: no cover - optional dep until installed
    _HEIF_OK = False

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp", ".heic", ".heif"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}

#: Frames per second to pull out of video before sharpness selection. Higher
#: than the final budget on purpose — selection needs candidates to reject.
VIDEO_EXTRACT_FPS = 4.0
# Historical ingest decoded at most 600 frames (4 fps → the first 150 s).  It
# is retained only when a caller explicitly opts into the legacy integer
# ``video_budget`` path; adaptive/default ingest passes ``None`` and keeps the
# complete duration under the global safety guard.
LEGACY_VIDEO_MAX_FRAMES = 600


@dataclass
class CameraGroup:
    """A set of images that share one physical camera and resolution."""

    name: str
    #: Native pixel size, used only for grouping and reporting.
    width: int
    height: int
    paths: list[Path] = field(default_factory=list)
    #: True when the group came from a video, which ``sfm`` matches
    #: sequentially rather than exhaustively.
    from_video: bool = False
    camera_model_hint: str = ""
    #: Stable source path, useful when two video files share a stem.
    source_path: str = ""
    #: Video duration is populated by extraction when ffprobe exposes it.
    duration_seconds: float | None = None
    #: Actual extraction rate after the long-video safety adjustment.
    extracted_fps: float | None = None
    #: True when extraction reduced FPS or evenly sampled an oversized output.
    safety_capped: bool = False
    #: Selection mode and counters for this group, filled by ``_ingest_impl``.
    selection: dict[str, object] = field(default_factory=dict)
    colour_processing: dict[str, object] = field(default_factory=dict)

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "count": len(self.paths),
            "from_video": self.from_video,
            "camera_model_hint": self.camera_model_hint,
            "source_path": self.source_path,
            "duration_seconds": self.duration_seconds,
            "extracted_fps": self.extracted_fps,
            "safety_capped": self.safety_capped,
            "selection": self.selection,
            "colour_processing": self.colour_processing,
        }


@dataclass
class IngestReport:
    groups: list[dict[str, object]]
    accepted: int
    rejected: int
    rejected_examples: list[dict[str, object]]
    notes: list[str]
    selection_preset: str = "balanced"
    video_budget: int | None = None
    counters: dict[str, int] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _exif_camera_label(path: Path) -> str:
    """A short 'Make Model FocalLength' label, or '' when EXIF is absent.

    Used to separate two cameras that happen to share a resolution, and to
    flag EXIF-stripped derivatives (which reconstruct worse and should
    generally not be mixed with originals).
    """
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return ""
            make = str(exif.get(271, "")).strip()   # Make
            model = str(exif.get(272, "")).strip()  # Model

            # FocalLength (0x920A) lives in the Exif sub-IFD (0x8769), not the
            # base IFD that getexif() returns — reading it off the top level
            # always yields nothing.
            focal_str = ""
            try:
                sub = exif.get_ifd(0x8769)
                focal = sub.get(0x920A)
                if focal:
                    focal_str = f"@{float(focal):.1f}mm"
            except (AttributeError, KeyError, TypeError, ValueError, ZeroDivisionError):
                pass

            return f"{make} {model}{focal_str}".strip()
    except (OSError, ValueError):
        return ""


def sharpness(path: Path, working_long_edge: int = 800) -> float:
    """Variance of the Laplacian — higher is sharper.

    Scored on a downscaled copy so the number reflects real structure rather
    than sensor noise, and so scoring hundreds of 25 MP files stays cheap.
    Values are only ever compared **within** a group; absolute magnitude
    depends on resolution and content, so cross-group thresholds are
    meaningless.
    """
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape
    long_edge = max(h, w)
    if long_edge > working_long_edge:
        scale = working_long_edge / long_edge
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(img, cv2.CV_64F).var())


def _slug(value: str, *, fallback: str = "group", limit: int = 48) -> str:
    """Make EXIF/video labels safe and readable as folder names."""

    result = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return (result[:limit].rstrip("_") or fallback)


def _source_files(source: Path, suffixes: set[str]) -> list[Path]:
    """Enumerate one source file or a directory without treating a file as empty."""

    source = Path(source)
    if source.is_file():
        return [source] if source.suffix.lower() in suffixes else []
    return [path for path in sorted(source.rglob("*")) if path.suffix.lower() in suffixes]


def _preserve_existing(path: Path) -> Path | None:
    """Move an existing derived output aside instead of deleting it."""

    path = Path(path)
    if not path.exists():
        return None
    suffix = uuid.uuid4().hex[:10]
    preserved = path.with_name(f"{path.name}.previous-{suffix}")
    while preserved.exists():
        preserved = path.with_name(f"{path.name}.previous-{uuid.uuid4().hex[:10]}")
    path.rename(preserved)
    logger.info("preserved previous derived output %s as %s", path, preserved)
    return preserved


def _stable_source_token(path: Path, source_root: Path) -> str:
    """Return a portable relative token for output naming and provenance."""

    try:
        root = source_root.resolve()
        absolute = path.resolve()
        if root.is_dir():
            return absolute.relative_to(root).as_posix().lower()
    except (OSError, ValueError):
        pass
    return path.name.lower()


def _video_safety_caps(videos: list[Path], source_root: Path) -> tuple[list[int], list[float | None]]:
    """Allocate one ingest-wide adaptive frame allowance across all videos."""

    if not videos:
        return [], []
    durations = [_probe_video_duration(video) for video in videos]
    total = GLOBAL_FRAME_SAFETY_CAP
    if len(videos) > total:
        # A pathological directory cannot give every clip even one frame
        # while honouring a hard global cap.  The caller records skipped clips.
        return [1] * total + [0] * (len(videos) - total), durations
    weights = np.asarray(
        [duration if duration is not None and np.isfinite(duration) and duration > 0 else 1.0 for duration in durations],
        dtype=np.float64,
    )
    weights /= weights.sum()
    raw = weights * total
    caps = np.maximum(1, np.floor(raw).astype(int))
    while int(caps.sum()) > total:
        candidates = np.flatnonzero(caps > 1)
        if not len(candidates):
            break
        index = max(candidates, key=lambda item: (caps[item] - raw[item], caps[item], -item))
        caps[index] -= 1
    # Largest remainders receive unused allowance; ties are source-order
    # stable so the same tree yields the same output.
    while int(caps.sum()) < total:
        index = max(range(len(caps)), key=lambda item: (raw[item] - np.floor(raw[item]), -item))
        caps[index] += 1
        raw[index] = np.floor(raw[index])
    return caps.tolist(), durations


def classify_sources(source_dir: Path, *, include: list[str] | None = None) -> list[CameraGroup]:
    """Group images under ``source_dir`` by (folder, resolution, EXIF label).

    Subdirectories are honoured as an explicit grouping hint: a folder that
    holds one resolution stays one group.  The camera label is part of the
    key, so two camera bodies with the same dimensions in one folder cannot
    silently inherit one another's intrinsics.  A missing label remains
    visible in the report and is never fabricated from a filename.
    """
    source_dir = Path(source_dir)
    groups: dict[tuple[str, int, int, str], CameraGroup] = {}

    candidates = _source_files(source_dir, IMAGE_SUFFIXES)
    if include:
        wanted = {w.lower() for w in include}
        candidates = [p for p in candidates if p.parent.name.lower() in wanted]

    for path in candidates:
        try:
            with Image.open(path) as im:
                width, height = im.size
        except (OSError, ValueError):
            logger.warning("skipping unreadable image %s", path)
            continue

        label = _exif_camera_label(path)
        folder = path.parent.name if path.parent != source_dir else "root"
        # Normalize only for grouping.  The original label is retained in the
        # report as the COLMAP hint, while a case/whitespace difference cannot
        # create duplicate groups for the same EXIF camera.
        label_key = " ".join(label.lower().split())
        key = (folder, width, height, label_key)

        group = groups.get(key)
        if group is None:
            group = CameraGroup(
                name=folder,
                width=width,
                height=height,
                camera_model_hint=label,
                source_path=str(path.parent),
            )
            groups[key] = group
        group.paths.append(path)

    # Keep the familiar folder name for the common one-group case.  Once a
    # folder contains multiple resolutions or EXIF camera labels, make each
    # output directory explicit and deterministic.
    ordered = sorted(groups.values(), key=lambda g: (-len(g.paths), g.name))
    folder_counts: dict[str, int] = {}
    for group in ordered:
        folder_counts[group.name] = folder_counts.get(group.name, 0) + 1
    seen: dict[str, int] = {}
    for group in ordered:
        base = group.name
        if folder_counts[base] > 1:
            label = _slug(group.camera_model_hint, fallback="no_exif")
            base = f"{base}_{label}_{group.width}x{group.height}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        group.name = base if count == 0 else f"{base}_{count + 1}"

    return ordered


def _probe_video_duration(video: Path) -> float | None:
    """Read container duration without decoding frames.

    A missing ``ffprobe`` or malformed metadata is not fatal: ffmpeg can
    still decode the clip, and the post-decode evenly-spaced guard below keeps
    the derived frame directory bounded when metadata is unreliable.
    """

    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        logger.warning("could not probe duration for %s; decode will use post-output safety sampling", video.name)
        return None
    if result.returncode != 0:
        logger.warning("ffprobe could not read duration for %s; decode will use post-output safety sampling", video.name)
        return None
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError):
        return None
    return duration if np.isfinite(duration) and duration > 0 else None


def video_colour_processing(video: Path) -> dict[str, object]:
    """Record HDR conversion before frames enter the SDR image pipeline.

    A fixed curve/peak keeps exposure consistent across frames. Original media
    is untouched; this is an explicitly recorded viewing/training derivative.
    See FFmpeg's tonemap documentation: tone mapping requires linear float RGB.
    """
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=color_transfer,color_primaries,color_space",
               "-of", "json", str(video)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
        document = json.loads(result.stdout)
        if not isinstance(document, dict):
            raise ValueError('ffprobe did not return a video metadata object')
        streams = document.get("streams", [])
        metadata = streams[0] if streams else {}
        if not isinstance(metadata, dict):
            raise ValueError('ffprobe returned malformed stream metadata')
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise RuntimeError(f"Cannot establish video colour metadata for {video.name}: {exc}") from exc
    hdr = metadata.get("color_transfer") in {"arib-std-b67", "smpte2084"}
    filters = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=hable:desat=0:peak=10,"
               "zscale=t=iec61966-2-1:m=bt709:r=full,format=rgb24") if hdr else ""
    return dict(source=metadata, hdr=hdr, method="hable-fixed-peak-10-to-srgb" if hdr else "native-sdr",
                filter=filters, output="sRGB 8-bit RGB" if hdr else "ffmpeg native PNG")


def extract_video_frames(
    video: Path,
    out_dir: Path,
    *,
    fps: float = VIDEO_EXTRACT_FPS,
    max_frames: int | None = None,
    on_frame=None,
    cancel_dir: Path | None = None,
    safety_cap: int = GLOBAL_FRAME_SAFETY_CAP,
    duration_seconds: float | None = None,
) -> CameraGroup:
    """Pull frames from a video with ffmpeg into its own camera group.

    Extracted at a higher rate than the final budget so sharpness selection
    has candidates to discard. Frames are written as PNG to avoid stacking a
    second generation of JPEG artefacts onto already-compressed video.
    """
    video = Path(video)
    out_dir = Path(out_dir)
    if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not np.isfinite(float(fps)) or float(fps) <= 0:
        raise ValueError("video extraction fps must be a positive number")
    if max_frames is not None and (
        isinstance(max_frames, bool) or not isinstance(max_frames, int) or max_frames <= 0
    ):
        raise ValueError("max_frames must be a positive integer or None")
    if max_frames is not None and max_frames > GLOBAL_FRAME_SAFETY_CAP:
        raise ValueError(
            f"max_frames {max_frames} exceeds the global frame safety cap ({GLOBAL_FRAME_SAFETY_CAP})"
        )
    if isinstance(safety_cap, bool) or not isinstance(safety_cap, int) or safety_cap <= 0:
        raise ValueError("safety_cap must be a positive integer")
    safety_cap = min(safety_cap, GLOBAL_FRAME_SAFETY_CAP)

    # Preserve prior derived frames instead of deleting them.  A stale
    # extraction directory can otherwise mix two runs; renaming it keeps a
    # recoverable copy and gives this run an empty destination.
    _preserve_existing(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%05d.png")

    if cancel_dir is not None:
        from .pipeline import check_cancel

        check_cancel(cancel_dir)
    duration = duration_seconds if duration_seconds is not None else _probe_video_duration(video)
    if cancel_dir is not None:
        from .pipeline import check_cancel

        check_cancel(cancel_dir)
    requested_fps = float(fps)
    effective_fps = requested_fps
    safety_capped = False
    if max_frames is None and duration is not None and duration * effective_fps > safety_cap:
        # Lowering FPS retains the whole timeline, including the final frame,
        # where a hard -frames:v cap would silently stop at the first 150 s on
        # the common 4 fps / 600-frame invocation.
        effective_fps = max(0.01, (safety_cap * 0.98) / duration)
        safety_capped = True

    colour = video_colour_processing(video)
    frame_filter = f"fps={effective_fps:g}"
    if colour["filter"]:
        frame_filter += "," + str(colour["filter"])
        logger.info("HDR video: converting prepared frames to sRGB using a fixed Hable curve")
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vf", frame_filter,
    ]
    # An explicit integer is the legacy reproducibility escape hatch.  The
    # adaptive/default path intentionally omits -frames:v so it cannot stop at
    # an arbitrary wall-clock time.
    if max_frames is not None:
        command.extend(["-frames:v", str(max_frames)])
    command.append(pattern)
    logger.info(
        "extracting frames from %s at %g fps%s",
        video.name,
        effective_fps,
        " (full-duration safety cap)" if safety_capped else "",
    )
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required for video ingest but was not found on PATH") from exc
    with process:
        try:
            # Poll rather than blocking for the whole decode so the pipeline's
            # cancel.request remains effective on long clips.
            stderr = ""
            while process.poll() is None:
                if cancel_dir is not None:
                    from .pipeline import check_cancel

                    check_cancel(cancel_dir)
                try:
                    _stdout, stderr = process.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    continue
            # If ffmpeg exited between polls, collect its final stderr too;
            # communicate() is idempotent after a completed process.
            _stdout, final_stderr = process.communicate()
            if final_stderr:
                stderr = final_stderr
        except OSError as exc:
            process.kill()
            process.wait()
            raise RuntimeError(f"could not decode {video.name}: {exc}") from exc
        except KeyboardInterrupt:
            process.kill()
            process.wait()
            raise
        if process.returncode != 0:
            raise RuntimeError(f"ffmpeg failed on {video.name}: {stderr.strip()[:400]}")

    frames = sorted(out_dir.glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"ffmpeg produced no frames from {video.name}")

    # Protect against a broken/variable-rate decoder producing more than the
    # calculated safety allowance.  Even sampling keeps the full duration and
    # avoids the old first-N-frames truncation.  This only removes derived
    # files in out_dir.
    if max_frames is None and len(frames) > safety_cap:
        indices = np.rint(np.linspace(0, len(frames) - 1, safety_cap)).astype(int)
        keep = {frames[index] for index in indices}
        for frame in frames:
            if frame not in keep:
                try:
                    frame.unlink()
                except OSError:
                    logger.warning("could not trim excess extracted frame %s", frame)
        frames = [frames[index] for index in indices if frames[index].exists()]
        safety_capped = True

    if on_frame:
        for frame in frames:
            on_frame(frame)

    with Image.open(frames[0]) as im:
        width, height = im.size

    logger.info("extracted %d frames (%dx%d)", len(frames), width, height)
    return CameraGroup(
        name=f"video_{video.stem}",
        width=width,
        height=height,
        paths=frames,
        from_video=True,
        camera_model_hint=f"video {video.name}",
        source_path=str(video),
        duration_seconds=duration,
        extracted_fps=effective_fps,
        safety_capped=safety_capped,
        colour_processing=colour,
    )


def select_sharpest(
    group: CameraGroup,
    budget: int,
    *,
    reject_ratio: float = 0.15,
    on_scored=None,
    cancel_dir: Path | None = None,
) -> tuple[list[Path], list[tuple[Path, float, str]]]:
    """Choose up to ``budget`` frames, favouring sharpness but keeping coverage.

    Two passes:

    1. Drop the bottom ``reject_ratio`` by sharpness outright — these are the
       genuinely smeared frames, and they actively harm feature matching.
    2. Split what remains into ``budget`` contiguous buckets and keep the
       sharpest of each. Buckets preserve coverage along the capture path; a
       plain global top-N would happily return ``budget`` frames of the one
       brightest corner and leave the rest of the room unregistered.

    Returns ``(kept, rejected)`` where each rejection carries a reason.
    """
    paths = list(group.paths)
    if not paths:
        return [], []

    scored = []
    for path in paths:
        if cancel_dir is not None:
            from .pipeline import check_cancel

            check_cancel(cancel_dir)
        score = sharpness(path)
        scored.append((path, score))
        if on_scored:
            on_scored(path, score)
    rejected: list[tuple[Path, float, str]] = []

    if len(scored) > budget:
        ranked = sorted(scored, key=lambda item: item[1])
        cut = int(len(ranked) * reject_ratio)
        blurred = {p for p, _ in ranked[:cut]}
        for path, score in scored:
            if path in blurred:
                rejected.append((path, score, f"blurred — bottom {int(reject_ratio * 100)}% by Laplacian variance"))
        scored = [(p, s) for p, s in scored if p not in blurred]

    if len(scored) <= budget:
        return [p for p, _ in scored], rejected

    # Bucket by position in the original (temporal / filename) order.
    kept: list[Path] = []
    edges = np.linspace(0, len(scored), budget + 1).astype(int)
    from itertools import pairwise

    for start, end in pairwise(edges):
        bucket = scored[start:end]
        if not bucket:
            continue
        best = max(bucket, key=lambda item: item[1])
        kept.append(best[0])
        for path, score in bucket:
            if path is not best[0]:
                rejected.append((path, score, "not sharpest in its coverage bucket"))

    return kept, rejected


def stage_group(
    group: CameraGroup,
    paths: list[Path],
    dest_root: Path,
    *,
    long_edge: int,
    on_staged=None,
) -> int:
    """Copy or downscale a group's chosen images into ``dest_root/<group>``.

    EXIF is carried through explicitly. PIL drops it on ``save`` unless it is
    passed back in, and COLMAP uses the EXIF focal length to seed intrinsics —
    losing it makes the solver start from a guess.
    """
    dest = dest_root / group.name
    dest.mkdir(parents=True, exist_ok=True)

    written = 0
    for path in paths:
        target = dest / f"{path.stem}.jpg"
        try:
            with Image.open(path) as im:
                exif = im.info.get("exif")
                icc = im.info.get("icc_profile")
                im = im.convert("RGB")
                width, height = im.size
                if max(width, height) > long_edge:
                    scale = long_edge / max(width, height)
                    im = im.resize(
                        (max(32, round(width * scale)), max(32, round(height * scale))),
                        Image.Resampling.LANCZOS,
                    )
                save_kwargs: dict[str, object] = {"quality": 95, "subsampling": 0}
                if exif:
                    save_kwargs["exif"] = exif
                if icc:
                    save_kwargs["icc_profile"] = icc
                im.save(target, "JPEG", **save_kwargs)
            written += 1
        except (OSError, ValueError) as exc:
            logger.warning("could not stage %s: %s", path, exc)
            if on_staged:
                on_staged(path, False)
            continue
        if on_staged:
            on_staged(path, True)

    return written


def ingest(
    source_dir: Path,
    out_dir: Path,
    *,
    long_edge: int,
    stills_budget: int = 400,
    video_budget: int | None = None,
    selection_preset: str | SelectionPreset = "balanced",
    include: list[str] | None = None,
) -> IngestReport:
    """Run ingest with adaptive selection unless a legacy budget is supplied."""

    from .construction import Progress
    with Progress(out_dir, "ingest") as observer:
        return _ingest_impl(source_dir, out_dir, long_edge=long_edge,
                            stills_budget=stills_budget, video_budget=video_budget,
                            selection_preset=selection_preset,
                            include=include, observer=observer)


def _probe_group_name(
    base: str,
    used: set[str],
    source_path: Path,
    source_root: Path | None = None,
) -> str:
    """Return a unique output group name without video-stem collisions."""

    candidate = base
    if candidate in used:
        token = _stable_source_token(source_path, source_root or source_path.parent)
        digest = hashlib.sha1(token.encode("utf-8", "replace")).hexdigest()[:8]
        candidate = f"{base}_{digest}"
        suffix = 2
        while candidate in used:
            candidate = f"{base}_{digest}_{suffix}"
            suffix += 1
    used.add(candidate)
    return candidate


def _progress(observer, **kwargs) -> None:
    """Update an optional progress observer used by the CLI and preview tests."""

    if observer is not None:
        observer.update(**kwargs)


def _ingest_impl(
    source_dir: Path,
    out_dir: Path,
    *,
    long_edge: int,
    stills_budget: int = 400,
    video_budget: int | None = None,
    selection_preset: str | SelectionPreset = "balanced",
    include: list[str] | None = None,
    observer=None,
) -> IngestReport:
    """Full ingest: classify, extract, select, stage.

    ``out_dir/images/<group>/`` is what ``sfm`` consumes — one subdirectory per
    camera group, which is how COLMAP is told there is more than one camera.
    """
    started = time.monotonic()
    source_dir = Path(source_dir)
    out_dir = Path(out_dir)
    if isinstance(long_edge, bool) or not isinstance(long_edge, int) or long_edge <= 0:
        raise ValueError("long_edge must be a positive integer")
    if isinstance(stills_budget, bool) or not isinstance(stills_budget, int) or stills_budget <= 0:
        raise ValueError("stills_budget must be a positive integer")
    preset = validate_selection_params(selection_preset, video_budget)
    source_resolved = source_dir.resolve()
    out_resolved = out_dir.resolve()
    if out_resolved == source_resolved or source_resolved in out_resolved.parents:
        raise ValueError("out_dir must be outside source_dir so ingest cannot delete source media")

    images_root = out_dir / "images"
    _preserve_existing(images_root)
    images_root.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    counters: dict[str, int] = {
        "source_images": 0,
        "source_videos": 0,
        "extracted_frames": 0,
        "video_extracted_frames": 0,
        "safety_capped_videos": 0,
        "scored_frames": 0,
        "accepted_frames": 0,
        "video_accepted_frames": 0,
        "staged_frames": 0,
        "rejected_frames": 0,
        "exposure_rejected": 0,
        "duplicate_rejected": 0,
        "coverage_bucket_rejected": 0,
        "legacy_budget_groups": 0,
        "adaptive_budget_groups": 0,
    }
    timings: dict[str, float] = {}
    groups: list[CameraGroup] = []
    preview = None
    succeeded = False
    try:
        classify_started = time.monotonic()
        groups = classify_sources(source_dir, include=include)
        timings["classify_seconds"] = time.monotonic() - classify_started
        counters["source_images"] = sum(len(group.paths) for group in groups)

        from .ingest_preview import IngestPreview

        preview = IngestPreview(out_dir, counters["source_images"])
        videos = _source_files(source_dir, VIDEO_SUFFIXES)
        if include:
            wanted = {w.lower() for w in include}
            videos = [v for v in videos if v.parent.name.lower() in wanted]
        counters["source_videos"] = len(videos)
        used_names = {group.name for group in groups}

        extract_started = time.monotonic()
        from .pipeline import check_cancel

        adaptive_caps, duration_hints = _video_safety_caps(videos, source_dir) if video_budget is None else ([], [])
        for video_index, video in enumerate(videos):
            check_cancel(out_dir.parent)
            _progress(observer, message="Extracting video frames", video=video.name)
            # ``stem`` alone is not unique (for example clip.MOV and clip.mp4
            # in different folders).  A short path digest isolates the derived
            # directories while the published group name stays readable.
            token = _stable_source_token(video, source_dir)
            digest = hashlib.sha1(token.encode("utf-8", "replace")).hexdigest()[:8]
            if video_budget is None and adaptive_caps[video_index] <= 0:
                notes.append(f"video '{video.name}' skipped because the global frame safety cap was exhausted.")
                continue
            frames_dir = out_dir / "_video_frames" / f"{_slug(video.stem, fallback='clip')}_{digest}"
            preview.extracting(video.name)
            group = extract_video_frames(
                video,
                frames_dir,
                max_frames=LEGACY_VIDEO_MAX_FRAMES if video_budget is not None else None,
                cancel_dir=out_dir.parent,
                safety_cap=adaptive_caps[video_index] if video_budget is None else GLOBAL_FRAME_SAFETY_CAP,
                duration_seconds=duration_hints[video_index] if video_budget is None else None,
            )
            group.name = _probe_group_name(group.name, used_names, video, source_dir)
            for path in group.paths:
                preview.extracted(group.name, path)
            groups.append(group)
            counters["extracted_frames"] += len(group.paths)
            counters["video_extracted_frames"] += len(group.paths)
            if group.safety_capped:
                counters["safety_capped_videos"] += 1
                notes.append(
                    f"video '{video.name}' exceeded the {GLOBAL_FRAME_SAFETY_CAP}-frame derived safety "
                    "cap; extraction FPS was reduced/evenly sampled across the full duration."
                )
            if group.duration_seconds is None:
                notes.append(
                    f"video '{video.name}' did not expose a readable duration; extracted frames were checked "
                    f"against the {GLOBAL_FRAME_SAFETY_CAP}-frame post-decode safety guard."
                )
        timings["extract_seconds"] = time.monotonic() - extract_started

        if not groups:
            raise RuntimeError(f"no usable images or video found under {source_dir}")

        select_started = time.monotonic()
        accepted = 0
        all_rejected: list[tuple[Path, float, str]] = []
        for group in groups:
            check_cancel(out_dir.parent)
            _progress(observer, message="Sorting images", group=group.name)

            def scored(path, score):
                check_cancel(out_dir.parent)
                preview.scored(path, score)

            if group.from_video and video_budget is None:
                result = select_adaptive(
                    group,
                    preset,
                    duration_seconds=group.duration_seconds,
                    sharpness_fn=sharpness,
                    on_scored=scored,
                )
                kept, rejected = result.kept, result.rejected
                group.selection = {
                    "mode": "adaptive",
                    "preset": preset.name,
                    **result.counters,
                }
                counters["adaptive_budget_groups"] += 1
                counters["scored_frames"] += result.counters.get("scored", 0)
                counters["exposure_rejected"] += result.counters.get("exposure_rejected", 0)
                counters["duplicate_rejected"] += result.counters.get("duplicate_rejected", 0)
                counters["coverage_bucket_rejected"] += result.counters.get("coverage_bucket_rejected", 0)
            else:
                budget = video_budget if group.from_video else stills_budget
                kept, rejected = select_sharpest(
                    group, budget, on_scored=scored, cancel_dir=out_dir.parent
                )
                group.selection = {
                    "mode": "legacy-sharpness",
                    "budget": budget,
                    "scored": len(group.paths),
                    "kept": len(kept),
                    "rejected": len(rejected),
                }
                if group.from_video:
                    counters["legacy_budget_groups"] += 1
                counters["scored_frames"] += len(group.paths)
            all_rejected.extend(rejected)
            counters["rejected_frames"] += len(rejected)
            for path, score, reason in rejected:
                preview.decision(group.name, path, "rejected", reason, round(score, 1))

            def staged(path, success, group_name=group.name):
                preview.decision(
                    group_name,
                    path,
                    "kept" if success else "rejected",
                    "Prepared for camera matching" if success else "Could not prepare this image",
                )

            written = stage_group(group, kept, images_root, long_edge=long_edge, on_staged=staged)
            accepted += written
            counters["accepted_frames"] += len(kept)
            if group.from_video:
                counters["video_accepted_frames"] += len(kept)
            counters["staged_frames"] += written
            _progress(observer, count=accepted, unit="prepared images", message="Prepared " + group.name)
            group.paths = kept
            logger.info(
                "group %-24s %4d kept / %4d rejected  (%dx%d, %s)",
                group.name, written, len(rejected), group.width, group.height,
                group.camera_model_hint or "no EXIF",
            )
            if not group.camera_model_hint and not group.from_video:
                notes.append(
                    f"group '{group.name}' has no EXIF — COLMAP cannot seed a focal "
                    "length and will solve intrinsics from scratch. Expect a weaker "
                    "reconstruction than an EXIF-bearing original."
                )
        timings["selection_and_stage_seconds"] = time.monotonic() - select_started

        if len(groups) > 1:
            notes.append(
                f"{len(groups)} camera groups staged. sfm must use "
                "--ImageReader.single_camera_per_folder so each gets its own intrinsics."
            )

        timings["total_seconds"] = time.monotonic() - started
        report = IngestReport(
            groups=[group.describe() for group in groups],
            accepted=accepted,
            rejected=len(all_rejected),
            rejected_examples=[
                {
                    "file": path.name,
                    "sharpness": round(score, 1),
                    "score": round(score, 4),
                    "reason": reason,
                }
                for path, score, reason in sorted(all_rejected, key=lambda item: item[1])[:20]
            ],
            notes=notes,
            selection_preset=preset.name,
            video_budget=video_budget,
            counters=counters,
            timings=timings,
        )
        (out_dir / "ingest.json").write_text(report.to_json(), encoding="utf-8")
        succeeded = True
        return report
    finally:
        if preview is not None and succeeded:
            preview.finish()
