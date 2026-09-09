"""Capture-session contract for Vitrine Capture → ingest.

A capture session is a folder (or zip of that folder) produced by the mobile
app *before* ingest. Photographs are the preservation master. LiDAR / ARKit
sidecars are guidance only and are never staged into ``source/``.

Layout::

    <session>/
      capture.json
      stills/<camera-group>/*.jpg|heic|...
      video/*.mov|mp4|...
      sidecar/                  # optional, not ingested

``runs/<name>/capture.json`` is the dashboard job ticket and is a different
file. The session document is stored as ``runs/<name>/capture-session.json``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from .ingest import IMAGE_SUFFIXES, VIDEO_SUFFIXES

logger = logging.getLogger(__name__)

SCHEMA = "vitrine/capture-session/1"
SESSION_FILENAME = "capture.json"
STAGED_FILENAME = "capture-session.json"

PASS_TYPES = frozenset({"orbit_chest", "orbit_knee", "loop_close", "detail", "video"})
SCREENS_POLICIES = frozenset({"off", "paused", "playing"})
MIRRORS_POLICIES = frozenset({"covered", "accepted", "mask-later"})
ROOM_SIZES = frozenset({"small", "room", "large"})
CHECKLIST_KEYS = (
    "three_or_more_views",
    "two_heights",
    "loop_closed",
    "detail_pass",
    "corners",
    "floor_edges",
    "exposure_locked",
)

_CHUNK = 1 << 20
_UUID = __import__("re").compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    __import__("re").IGNORECASE,
)
_HEX64 = __import__("re").compile(r"^[0-9a-f]{64}$")


class CaptureSessionError(ValueError):
    """Raised when a capture session fails validation."""


@dataclass
class CaptureSession:
    """A validated session ready to stage into ``source/``."""

    root: Path
    document: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def session_id(self) -> str:
        return str(self.document["session_id"])

    @property
    def title(self) -> str:
        return str(self.document.get("title") or "Untitled capture")

    @property
    def subject(self) -> str:
        return str(self.document.get("subject") or "Not recorded.")

    @property
    def stills(self) -> list[dict[str, Any]]:
        return [f for f in self.document["files"] if f["role"] == "still"]

    @property
    def videos(self) -> list[dict[str, Any]]:
        return [f for f in self.document["files"] if f["role"] == "video"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _posix(rel: str) -> str:
    return rel.replace("\\", "/")


def _safe_relative(rel: Any, field: str) -> PurePosixPath:
    if not isinstance(rel, str) or not rel.strip():
        raise CaptureSessionError(f"{field} must be a non-empty POSIX path")
    text = _posix(rel)
    if "\\" in rel or __import__("re").match(r"^[A-Za-z]:", text):
        raise CaptureSessionError(f"{field} must be a POSIX relative path, got {rel!r}")
    pp = PurePosixPath(text)
    if pp.is_absolute() or any(part in ("..", "") for part in pp.parts):
        raise CaptureSessionError(f"{field} must be relative with no '..': {rel!r}")
    return pp


def _require(doc: dict[str, Any], key: str, typ: type | tuple[type, ...]) -> Any:
    if key not in doc:
        raise CaptureSessionError(f"capture.json missing {key!r}")
    value = doc[key]
    if not isinstance(value, typ):
        raise CaptureSessionError(f"{key} must be {typ}, got {type(value).__name__}")
    return value


def _find_session_root(extracted: Path) -> Path:
    direct = extracted / SESSION_FILENAME
    if direct.is_file():
        return extracted
    candidates = [p.parent for p in extracted.rglob(SESSION_FILENAME) if p.is_file()]
    # Ignore anything under __MACOSX.
    candidates = [p for p in candidates if "__MACOSX" not in p.parts]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise CaptureSessionError("zip/folder has no capture.json")
    raise CaptureSessionError("zip contains more than one capture.json")


def extract_zip_safe(zip_path: Path, dest: Path) -> None:
    """Extract ``zip_path`` into ``dest``, rejecting traversal and absolute names."""
    dest = dest.resolve()
    try:
        archive = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise CaptureSessionError(f"not a zip file: {zip_path}") from exc
    with archive:
        for info in archive.infolist():
            name = _posix(info.filename)
            if name.endswith("/"):
                continue
            pp = PurePosixPath(name)
            if name.startswith("/") or pp.is_absolute() or any(part in ("..", "") for part in pp.parts):
                raise CaptureSessionError(f"unsafe zip path: {info.filename!r}")
            target = (dest / name).resolve()
            if not target.is_relative_to(dest):
                raise CaptureSessionError(f"unsafe zip path: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)


@contextmanager
def open_session(path: Path) -> Iterator[Path]:
    """Yield the session root for a folder or a zip of that folder."""
    path = Path(path)
    if path.is_dir():
        yield path
        return
    if path.is_file() and path.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory(prefix="vitrine-session-")
        try:
            extract_zip_safe(path, Path(tmp.name))
            yield _find_session_root(Path(tmp.name))
        finally:
            tmp.cleanup()
        return
    raise CaptureSessionError(f"session must be a folder or .zip, got {path}")


def _media_role(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "still"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return None


def _scan_media(root: Path) -> tuple[list[Path], list[Path]]:
    stills: list[Path] = []
    videos: list[Path] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.is_symlink():
            raise CaptureSessionError(f"symlinks are not allowed: {path.relative_to(root).as_posix()}")
        rel = path.relative_to(root)
        parts = rel.parts
        if parts[0] == "sidecar":
            continue
        if path.name == SESSION_FILENAME and len(parts) == 1:
            continue
        role = _media_role(path)
        if role is None:
            continue
        if parts[0] not in {"stills", "video"}:
            raise CaptureSessionError(
                f"{rel.as_posix()} is not under stills/ or video/ — "
                "a flattened session would collapse camera groups"
            )
        if parts[0] == "stills" and role != "still":
            raise CaptureSessionError(f"video file inside stills/: {rel.as_posix()}")
        if parts[0] == "video" and role != "video":
            raise CaptureSessionError(f"image file inside video/: {rel.as_posix()}")
        (stills if role == "still" else videos).append(path)
    return stills, videos


def _validate_document(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        raise CaptureSessionError("capture.json must be a JSON object")
    schema = doc.get("schema")
    if schema != SCHEMA:
        raise CaptureSessionError(f"schema must be {SCHEMA!r}, got {schema!r}")
    session_id = _require(doc, "session_id", str)
    if not _UUID.match(session_id):
        raise CaptureSessionError(f"session_id is not a UUID: {session_id!r}")
    _require(doc, "title", str)
    screens = _require(doc, "screens_policy", str)
    if screens not in SCREENS_POLICIES:
        raise CaptureSessionError(
            f"screens_policy must be one of {sorted(SCREENS_POLICIES)}, got {screens!r}"
        )
    mirrors = _require(doc, "mirrors_policy", str)
    if mirrors not in MIRRORS_POLICIES:
        raise CaptureSessionError(
            f"mirrors_policy must be one of {sorted(MIRRORS_POLICIES)}, got {mirrors!r}"
        )
    locked = _require(doc, "locked_settings", dict)
    for key in ("ae", "awb", "af"):
        if locked.get(key) is not True:
            raise CaptureSessionError(
                f"locked_settings.{key} must be true — unlocked exposure/colour/focus "
                "cannot be repaired at ingest"
            )
    if locked.get("flash") not in (None, "off", False):
        raise CaptureSessionError("flash must be off")
    zoom = locked.get("zoom", 1)
    if not isinstance(zoom, (int, float)) or abs(float(zoom) - 1.0) > 1e-6:
        raise CaptureSessionError("zoom must be 1.0 — zooming mid-pass changes intrinsics")
    device = _require(doc, "device", dict)
    if "model" not in device:
        raise CaptureSessionError("device.model is required")
    passes = _require(doc, "passes", list)
    for item in passes:
        if not isinstance(item, dict) or item.get("type") not in PASS_TYPES:
            raise CaptureSessionError(
                f"pass type must be one of {sorted(PASS_TYPES)}, got {item!r}"
            )
    checklist = _require(doc, "checklist", dict)
    for key in CHECKLIST_KEYS:
        if key not in checklist:
            raise CaptureSessionError(f"checklist missing {key!r}")
        if not isinstance(checklist[key], bool):
            raise CaptureSessionError(f"checklist.{key} must be a boolean")
    coverage = _require(doc, "coverage", dict)
    if not isinstance(coverage.get("stills_count"), int) or coverage["stills_count"] < 0:
        raise CaptureSessionError("coverage.stills_count must be a non-negative integer")
    files = _require(doc, "files", list)
    if not files:
        raise CaptureSessionError("capture.json files list is empty")
    seen: set[str] = set()
    still_count = 0
    for entry in files:
        if not isinstance(entry, dict):
            raise CaptureSessionError("files entries must be objects")
        rel = _safe_relative(entry.get("path"), "files.path")
        path_text = rel.as_posix()
        if path_text in seen:
            raise CaptureSessionError(f"duplicate files.path {path_text}")
        seen.add(path_text)
        role = entry.get("role")
        if role not in {"still", "video"}:
            raise CaptureSessionError(f"files.role must be still or video, got {role!r}")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not _HEX64.match(digest):
            raise CaptureSessionError(f"files.sha256 must be 64 lowercase hex, got {digest!r}")
        if role == "still":
            still_count += 1
            if rel.parts[0] != "stills":
                raise CaptureSessionError(f"still must live under stills/, got {path_text}")
        else:
            if rel.parts[0] != "video":
                raise CaptureSessionError(f"video must live under video/, got {path_text}")
    if still_count == 0:
        raise CaptureSessionError("session has no stills — ingest has nothing to reconstruct from")
    if coverage["stills_count"] != still_count:
        raise CaptureSessionError(
            f"coverage.stills_count is {coverage['stills_count']} but files lists {still_count} stills"
        )
    room = doc.get("room_size")
    if room is not None and room not in ROOM_SIZES:
        raise CaptureSessionError(f"room_size must be one of {sorted(ROOM_SIZES)}, got {room!r}")
    return doc


def _exif_warning(path: Path) -> str | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = im.getexif()
            if not exif:
                return path.name
    except Exception:  # noqa: BLE001 - warning path only
        return path.name
    return None


def validate_session(root: Path) -> CaptureSession:
    """Load and fully validate a session folder. Raises on any hard error."""
    root = Path(root)
    manifest_path = root / SESSION_FILENAME
    if not manifest_path.is_file():
        raise CaptureSessionError("no capture.json in session")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise CaptureSessionError("capture.json is not UTF-8") from exc
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CaptureSessionError(f"capture.json is not JSON: {exc}") from exc
    doc = _validate_document(doc)

    stills, videos = _scan_media(root)
    on_disk = {p.relative_to(root).as_posix(): p for p in stills + videos}
    listed = {entry["path"]: entry for entry in doc["files"]}
    missing = sorted(set(listed) - set(on_disk))
    extra = sorted(set(on_disk) - set(listed))
    if missing:
        raise CaptureSessionError(f"listed files missing from disk: {', '.join(missing[:8])}")
    if extra:
        raise CaptureSessionError(
            f"unstated media under stills/ or video/: {', '.join(extra[:8])}"
        )

    for rel, entry in listed.items():
        path = on_disk[rel]
        size = path.stat().st_size
        if entry.get("bytes") not in (None, size):
            raise CaptureSessionError(f"size mismatch for {rel}: listed {entry.get('bytes')}, disk {size}")
        digest = sha256_file(path)
        if digest != entry["sha256"]:
            raise CaptureSessionError(f"hash mismatch for {rel}")

    warnings: list[str] = []
    checklist = doc["checklist"]
    if not checklist.get("loop_closed"):
        warnings.append("loop was not closed — reconstruction may drift")
    if not checklist.get("two_heights"):
        warnings.append("only one capture height — tops and undersides will be weak")
    if not checklist.get("detail_pass"):
        warnings.append("no detail pass — fine text is unlikely to be readable")
    if not checklist.get("three_or_more_views"):
        warnings.append("surfaces may not have three or more views")
    if not checklist.get("corners") or not checklist.get("floor_edges"):
        warnings.append("corners or floor–wall junctions may be missing")
    target = doc.get("coverage", {}).get("stills_target")
    if isinstance(target, int) and target > 0 and len(stills) < target:
        warnings.append(f"{len(stills)} stills captured, target was {target}")
    no_exif = [_exif_warning(p) for p in stills]
    missing_exif = [name for name in no_exif if name]
    if missing_exif:
        warnings.append(
            f"{len(missing_exif)} still(s) have no EXIF — COLMAP cannot seed focal length"
        )
    if not videos:
        warnings.append("no video glue pass — stills will have to close coverage alone")
    lidar_used = bool(doc.get("coverage", {}).get("lidar_used"))
    if lidar_used and not doc.get("device", {}).get("lidar_available"):
        warnings.append("coverage.lidar_used is true but device.lidar_available is false")

    return CaptureSession(root=root, document=doc, warnings=warnings)


def load_session(path: Path) -> CaptureSession:
    """Validate a folder or zip and return the session. Zip contents are copied
    to a durable temp root owned by the returned object only for the duration
    of staging — prefer :func:`open_session` + :func:`validate_session` when
    the caller already has a folder.
    """
    path = Path(path)
    if path.is_dir():
        return validate_session(path)
    with open_session(path) as root:
        session = validate_session(root)
        # Copy out of the temporary extract so the object outlives the context.
        durable = Path(tempfile.mkdtemp(prefix="vitrine-session-keep-"))
        shutil.copytree(root, durable, dirs_exist_ok=True)
        session.root = durable
        return session


def stage_session(session: CaptureSession, dest_source: Path) -> Path:
    """Copy stills/ and video/ byte-identical into ``dest_source``.

    Sidecar material is not copied — ingest would otherwise treat rejected
    stills as source. Writes ``capture-session.json`` next to ``dest_source``.
    """
    dest_source = Path(dest_source)
    parent = dest_source.parent
    parent.mkdir(parents=True, exist_ok=True)
    if dest_source.is_symlink() or (dest_source.exists() and not dest_source.is_dir()):
        raise CaptureSessionError(f"session destination is not a directory: {dest_source}")
    # A second import into a run must never silently merge camera groups or
    # stale video with a new phone session. An empty directory is allowed
    # because the dashboard creates it before validation; anything inside it
    # is an explicit recovery decision for the operator.
    if dest_source.exists() and any(dest_source.iterdir()):
        raise CaptureSessionError(
            f"session destination is not empty: {dest_source} — "
            "refusing to mix captures; choose a new run or clear this run explicitly"
        )
    staged = parent / STAGED_FILENAME
    if staged.exists():
        raise CaptureSessionError(
            f"{staged} already exists — refusing to replace an existing capture session"
        )

    staging = Path(tempfile.mkdtemp(prefix=f".{dest_source.name}.session-", dir=parent))
    manifest_tmp = parent / f".{STAGED_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        for folder in ("stills", "video"):
            src = session.root / folder
            if src.is_dir():
                shutil.copytree(src, staging / folder, copy_function=shutil.copy2)
        manifest_tmp.write_text(json.dumps(session.document, indent=2) + "\n", encoding="utf-8")
        if dest_source.exists():
            # It was checked above and is intentionally empty. If another
            # worker populated it meanwhile, rmdir fails without deleting data.
            dest_source.rmdir()
        os.replace(staging, dest_source)
        os.replace(manifest_tmp, staged)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        manifest_tmp.unlink(missing_ok=True)
        raise
    logger.info(
        "staged session %s → %s (%d stills, %d video)",
        session.session_id,
        dest_source,
        len(session.stills),
        len(session.videos),
    )
    return dest_source


def import_session(path: Path, run_dir: Path) -> CaptureSession:
    """Validate ``path`` and stage it into ``run_dir/source``."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    with open_session(path) as root:
        session = validate_session(root)
        stage_session(session, run_dir / "source")
        return session
