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

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

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

    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "count": len(self.paths),
            "from_video": self.from_video,
            "camera_model_hint": self.camera_model_hint,
        }


@dataclass
class IngestReport:
    groups: list[dict[str, object]]
    accepted: int
    rejected: int
    rejected_examples: list[dict[str, object]]
    notes: list[str]

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


def classify_sources(source_dir: Path, *, include: list[str] | None = None) -> list[CameraGroup]:
    """Group images under ``source_dir`` by (resolution, EXIF camera label).

    Subdirectories are honoured as an explicit grouping hint: a folder that
    holds one resolution stays one group and keeps its folder name, which is
    what makes a hand-organised ``source/`` tree behave predictably.
    """
    source_dir = Path(source_dir)
    groups: dict[tuple[str, int, int], CameraGroup] = {}

    candidates = [p for p in sorted(source_dir.rglob("*")) if p.suffix.lower() in IMAGE_SUFFIXES]
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
        key = (folder, width, height)

        group = groups.get(key)
        if group is None:
            group = CameraGroup(name=folder, width=width, height=height, camera_model_hint=label)
            groups[key] = group
        group.paths.append(path)

    # Disambiguate groups that share a folder name but differ in resolution.
    ordered = sorted(groups.values(), key=lambda g: (-len(g.paths), g.name))
    seen: dict[str, int] = {}
    for group in ordered:
        count = seen.get(group.name, 0)
        seen[group.name] = count + 1
        if count:
            group.name = f"{group.name}_{group.width}x{group.height}"

    return ordered


def extract_video_frames(
    video: Path,
    out_dir: Path,
    *,
    fps: float = VIDEO_EXTRACT_FPS,
    max_frames: int = 600,
) -> CameraGroup:
    """Pull frames from a video with ffmpeg into its own camera group.

    Extracted at a higher rate than the final budget so sharpness selection
    has candidates to discard. Frames are written as PNG to avoid stacking a
    second generation of JPEG artefacts onto already-compressed video.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%05d.png")

    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(video),
        "-vf", f"fps={fps:g}",
        "-frames:v", str(max_frames),
        pattern,
    ]
    logger.info("extracting frames from %s at %g fps", video.name, fps)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed on {video.name}: {result.stderr.strip()[:400]}")

    frames = sorted(out_dir.glob("frame_*.png"))
    if not frames:
        raise RuntimeError(f"ffmpeg produced no frames from {video.name}")

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
    )


def select_sharpest(
    group: CameraGroup,
    budget: int,
    *,
    reject_ratio: float = 0.15,
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

    scored = [(p, sharpness(p)) for p in paths]
    rejected: list[tuple[Path, float, str]] = []

    if len(scored) > budget:
        ranked = sorted(scored, key=lambda item: item[1])
        cut = int(len(ranked) * reject_ratio)
        blurred = {p for p, _ in ranked[:cut]}
        for path, score in scored:
            if path in blurred:
                rejected.append((path, score, "blurred — bottom %d%% by Laplacian variance" % int(reject_ratio * 100)))
        scored = [(p, s) for p, s in scored if p not in blurred]

    if len(scored) <= budget:
        return [p for p, _ in scored], rejected

    # Bucket by position in the original (temporal / filename) order.
    kept: list[Path] = []
    edges = np.linspace(0, len(scored), budget + 1).astype(int)
    for start, end in zip(edges[:-1], edges[1:]):
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

    return written


def ingest(
    source_dir: Path,
    out_dir: Path,
    *,
    long_edge: int,
    stills_budget: int = 400,
    video_budget: int = 200,
    include: list[str] | None = None,
) -> IngestReport:
    """Full ingest: classify, extract, select, stage.

    ``out_dir/images/<group>/`` is what ``sfm`` consumes — one subdirectory per
    camera group, which is how COLMAP is told there is more than one camera.
    """
    source_dir = Path(source_dir)
    out_dir = Path(out_dir)
    images_root = out_dir / "images"
    if images_root.exists():
        shutil.rmtree(images_root)
    images_root.mkdir(parents=True, exist_ok=True)

    notes: list[str] = []
    groups = classify_sources(source_dir, include=include)

    videos = [p for p in sorted(source_dir.rglob("*")) if p.suffix.lower() in VIDEO_SUFFIXES]
    if include:
        wanted = {w.lower() for w in include}
        videos = [v for v in videos if v.parent.name.lower() in wanted]
    for video in videos:
        frames_dir = out_dir / "_video_frames" / video.stem
        groups.append(extract_video_frames(video, frames_dir))

    if not groups:
        raise RuntimeError(f"no usable images or video found under {source_dir}")

    accepted = 0
    all_rejected: list[tuple[Path, float, str]] = []

    for group in groups:
        budget = video_budget if group.from_video else stills_budget
        kept, rejected = select_sharpest(group, budget)
        all_rejected.extend(rejected)
        written = stage_group(group, kept, images_root, long_edge=long_edge)
        accepted += written
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

    if len(groups) > 1:
        notes.append(
            f"{len(groups)} camera groups staged. sfm must use "
            "--ImageReader.single_camera_per_folder so each gets its own intrinsics."
        )

    report = IngestReport(
        groups=[g.describe() for g in groups],
        accepted=accepted,
        rejected=len(all_rejected),
        rejected_examples=[
            {"file": p.name, "sharpness": round(s, 1), "reason": r}
            for p, s, r in sorted(all_rejected, key=lambda item: item[1])[:20]
        ],
        notes=notes,
    )
    (out_dir / "ingest.json").write_text(report.to_json(), encoding="utf-8")
    return report
