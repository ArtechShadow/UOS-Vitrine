"""Local web UI for creating captures and inspecting runs on disk.

Surfaces what the pipeline has produced — stage reports, metrics, artefacts,
and the interactive splat viewer. New image/video uploads start the existing
CLI pipeline in a background process rather than reimplementing its stages.

Start with::

    python -m vitrine ui
    python -m vitrine ui --port 8765 --open
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import __version__, profiles

logger = logging.getLogger(__name__)

_DISPOSITION_PARAM = re.compile(r'(\w+)\s*=\s*("(?:\\.|[^"])*"|[^;]+)')


class _FormField:
    """One multipart field. File parts expose ``filename`` and ``file``."""

    __slots__ = ("filename", "file", "value")

    def __init__(
        self,
        *,
        filename: str | None,
        payload: bytes | None = None,
        file_obj: Any | None = None,
        value: str | None = None,
    ) -> None:
        self.filename = filename
        if filename is not None:
            self.value = None
            self.file = file_obj or tempfile.SpooledTemporaryFile(
                max_size=_MULTIPART_SPOOL_BYTES, mode="w+b"
            )
            if payload:
                self.file.write(payload)
                self.file.seek(0)
        else:
            self.value = value if value is not None else (payload or b"").decode(
                "utf-8", errors="replace"
            )
            self.file = None


class _MultipartForm:
    """cgi.FieldStorage subset used by the capture upload POST handler."""

    def __init__(self, fields: dict[str, list[_FormField]]) -> None:
        self._fields = fields

    def __contains__(self, name: str) -> bool:
        return name in self._fields

    def __getitem__(self, name: str) -> _FormField | list[_FormField]:
        items = self._fields[name]
        return items[0] if len(items) == 1 else items

    def getfirst(self, name: str, default: str = "") -> str:
        items = self._fields.get(name)
        if not items:
            return default
        field = items[0]
        if field.filename is not None:
            return field.filename
        return field.value if field.value is not None else default

    def close(self) -> None:
        """Close temporary upload files owned by this request."""
        for items in self._fields.values():
            for field in items:
                if field.file is not None:
                    field.file.close()


def _disposition_params(header: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for match in _DISPOSITION_PARAM.finditer(header):
        raw = match.group(2).strip()
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1].replace('\\"', '"')
        params[match.group(1).lower()] = raw
    return params


_MULTIPART_CHUNK = 1024 * 1024
_MULTIPART_SPOOL_BYTES = 1024 * 1024
_MULTIPART_TEXT_BYTES = 1024 * 1024
_MULTIPART_HEADER_BYTES = 64 * 1024
_MULTIPART_PARTS = 2048
# A capture can contain a long 4K/60 video, but an unbounded request would let
# a typo or a broken browser consume the whole workstation disk. The parser
# streams each file to a SpooledTemporaryFile and rejects the request before
# reading it when its declared size is unreasonable.
MAX_MULTIPART_BYTES = 8 * 1024**3


class _MultipartTooLarge(ValueError):
    """A request exceeded the dashboard's bounded upload budget."""


class _LimitedBody:
    """Read at most one HTTP request body while retaining parser pushback."""

    def __init__(self, fp: Any, length: int) -> None:
        self.fp = fp
        self.remaining = length
        self._buffer = bytearray()

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.remaining
        size = min(size, self.remaining)
        if size <= 0:
            return b""
        parts: list[bytes] = []
        if self._buffer:
            take = min(size, len(self._buffer))
            parts.append(bytes(self._buffer[:take]))
            del self._buffer[:take]
            size -= take
        if size:
            # ``BufferedReader.read(n)`` may wait for all *n* bytes when the
            # request is still open. ``read1`` returns the bytes already
            # buffered by the HTTP server, which keeps small multipart
            # requests from deadlocking while the parser waits for a 1 MiB
            # chunk that can never arrive.
            read1 = getattr(self.fp, "read1", None)
            chunk = read1(size) if callable(read1) else self.fp.read(size)
            if chunk:
                parts.append(chunk)
        data = b"".join(parts)
        # ``remaining`` tracks unread request bytes, including parser
        # pushback. Consume both buffered and newly-read bytes here.
        self.remaining -= len(data)
        return data

    def unread(self, data: bytes) -> None:
        if data:
            self._buffer[:0] = data
            self.remaining += len(data)


def _read_multipart_line(reader: _LimitedBody, limit: int = _MULTIPART_HEADER_BYTES) -> bytes:
    """Read one header/delimiter line without allowing a huge line in RAM."""
    line = bytearray()
    while len(line) <= limit:
        chunk = reader.read(min(8192, limit + 1 - len(line)))
        if not chunk:
            return bytes(line)
        newline = chunk.find(b"\n")
        if newline >= 0:
            line.extend(chunk[: newline + 1])
            reader.unread(chunk[newline + 1 :])
            return bytes(line)
        line.extend(chunk)
    raise ValueError("multipart header line is too long")


class _MultipartPart:
    """Spool one file part to disk, while keeping small text fields bounded."""

    def __init__(self, filename: str | None) -> None:
        self.filename = filename
        self.file = (
            tempfile.SpooledTemporaryFile(max_size=_MULTIPART_SPOOL_BYTES, mode="w+b")
            if filename is not None
            else None
        )
        self.text = bytearray()

    def write(self, data: bytes) -> None:
        if not data:
            return
        if self.file is not None:
            self.file.write(data)
            return
        if len(self.text) + len(data) > _MULTIPART_TEXT_BYTES:
            raise ValueError("multipart text field is too large")
        self.text.extend(data)

    def finish(self) -> _FormField:
        if self.file is not None:
            self.file.seek(0)
            file_obj = self.file
            self.file = None
            return _FormField(filename=self.filename, file_obj=file_obj)
        return _FormField(filename=None, value=bytes(self.text).decode("utf-8", errors="replace"))

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None


def _copy_multipart_part(reader: _LimitedBody, boundary: bytes, sink: _MultipartPart) -> bool:
    """Copy through the next multipart delimiter and return whether it is final."""
    marker = b"\r\n--" + boundary
    keep = len(marker) + 2
    pending = bytearray()
    while True:
        chunk = reader.read(_MULTIPART_CHUNK)
        if chunk:
            pending.extend(chunk)
        while True:
            index = pending.find(marker)
            if index < 0:
                # Keep enough bytes to match a delimiter split across reads.
                if len(pending) > keep:
                    sink.write(bytes(pending[:-keep]))
                    del pending[:-keep]
                break
            suffix_start = index + len(marker)
            if len(pending) < suffix_start + 2:
                if index:
                    sink.write(bytes(pending[:index]))
                    del pending[:index]
                break
            suffix = bytes(pending[suffix_start : suffix_start + 2])
            if suffix not in (b"--", b"\r\n"):
                # This only looks like a boundary in the payload; keep scanning.
                sink.write(bytes(pending[: index + 1]))
                del pending[: index + 1]
                continue
            sink.write(bytes(pending[:index]))
            remainder = bytes(pending[suffix_start + 2 :])
            pending.clear()
            if suffix == b"\r\n":
                # The next part's header begins immediately after the delimiter.
                reader.unread(remainder)
                return False
            # A closing delimiter may have a trailing CRLF. Discard only bytes
            # still belonging to this request and drain the underlying reader.
            while reader.read(_MULTIPART_CHUNK):
                pass
            return True
        if not chunk:
            raise ValueError("multipart boundary missing or truncated")


def _parse_multipart_form(fp: Any, headers: Any) -> _MultipartForm:
    """Parse multipart uploads as a bounded stream without the removed ``cgi``."""
    content_type = headers.get("Content-Type", "")
    match = re.search(r"boundary\s*=\s*(\"[^\"]+\"|[^\s;]+)", content_type, re.I)
    if not match:
        raise ValueError("multipart boundary missing")
    boundary = match.group(1).strip().strip('"').encode("ascii", errors="strict")
    try:
        length = int(headers.get("Content-Length", "0") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid content length") from exc
    if length <= 0:
        raise ValueError("invalid content length")
    if length > MAX_MULTIPART_BYTES:
        raise _MultipartTooLarge(
            f"upload is too large ({length / 2**30:.2f} GiB; maximum is "
            f"{MAX_MULTIPART_BYTES / 2**30:.0f} GiB)"
        )

    reader = _LimitedBody(fp, length)
    fields: dict[str, list[_FormField]] = {}
    owned: list[_MultipartPart] = []
    try:
        first = _read_multipart_line(reader).rstrip(b"\r\n")
        opening = b"--" + boundary
        if first != opening:
            raise ValueError("multipart opening boundary missing")
        while True:
            header_lines: list[bytes] = []
            header_bytes = 0
            while True:
                line = _read_multipart_line(reader)
                if not line:
                    raise ValueError("multipart headers truncated")
                header_bytes += len(line)
                if header_bytes > _MULTIPART_HEADER_BYTES:
                    raise ValueError("multipart headers are too large")
                stripped = line.rstrip(b"\r\n")
                if not stripped:
                    break
                header_lines.append(stripped)

            disposition = ""
            for line in header_lines:
                if line.lower().startswith(b"content-disposition:"):
                    disposition = line.split(b":", 1)[1].decode("utf-8", errors="replace").strip()
                    break
            params = _disposition_params(disposition)
            name = params.get("name")
            filename = params.get("filename") or None
            sink = _MultipartPart(filename)
            owned.append(sink)
            final = _copy_multipart_part(reader, boundary, sink)
            field = sink.finish()
            owned.remove(sink)
            if name:
                fields.setdefault(name, []).append(field)
            else:
                field.file.close() if field.file is not None else None
            if sum(len(items) for items in fields.values()) > _MULTIPART_PARTS:
                raise ValueError("multipart form has too many parts")
            if final:
                return _MultipartForm(fields)
    except Exception:
        for sink in owned:
            sink.close()
        for items in fields.values():
            for field in items:
                if field.file is not None:
                    field.file.close()
        raise


# UI assets live next to this module.
UI_DIR = Path(__file__).resolve().parent / "ui"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# progress.json is rewritten every 500 training steps (see train.py). Even on
# the 3060 Laptop (~500 ms/iter) that is roughly every four minutes. Anything
# older than this is treated as an interrupted run, not live training — a
# crashed/killed process leaves the file behind and must not read as "Building".
PROGRESS_FRESH_SECONDS = 10 * 60

UPLOAD_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".bmp",
    ".heic", ".heif", ".mp4", ".mov", ".m4v", ".avi", ".mkv",
}
_WINDOWS_DEVICE_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _run_slug(value: str) -> str:
    """Return a filesystem-safe run name derived from a human title."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    slug = slug[:64] or "new-capture"
    # Windows reserves these names even when a suffix is supplied. Keep the
    # generated folder valid on the demo host and other local workstations.
    if slug.upper().split(".", 1)[0] in _WINDOWS_DEVICE_NAMES:
        slug = f"{slug}-run"
    return slug


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _pipeline_record(run_dir: Path) -> dict[str, Any] | None:
    """Read the persisted pipeline state without inferring completion from files."""
    record = _read_json(Path(run_dir) / "pipeline.json")
    return record if isinstance(record, dict) else None


def _pipeline_state(run_dir: Path) -> str | None:
    record = _pipeline_record(run_dir)
    state = record.get("state") if record else None
    return state if isinstance(state, str) else None


def _pid_is_running(pid: Any) -> bool:
    """Best-effort local process check used only to avoid duplicate resumes."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _preflight_payload(
    *, source: Path | None, output: Path, full: bool = False
) -> dict[str, Any]:
    """Run the cheap dashboard preflight, or the explicit full one."""
    try:
        from .preflight import check_preflight

        report = check_preflight(
            source,
            output,
            require_gpu=True,
            check_sfm=full,
            # A browser poll must never trigger the first gsplat JIT compile.
            check_training=full,
            check_media=source is not None,
        )
        report["scope"] = "full" if full else "quick"
        return report
    except Exception as exc:  # noqa: BLE001 - surface a truthful UI error
        logger.exception("preflight failed")
        return {
            "ready": False,
            "scope": "full" if full else "quick",
            "checks": [{
                "name": "Preflight service",
                "status": "error",
                "detail": str(exc),
                "fix": "Run vitrine preflight from the project environment and review its output.",
            }],
            "hardware": {},
        }


def _capture_command(run_dir: Path, *, resume: bool) -> list[str]:
    """Rebuild a dashboard job command from its persisted capture ticket."""
    run_dir = Path(run_dir).resolve()
    ticket = _read_json(run_dir / "capture.json") or {}
    pipeline = _pipeline_record(run_dir) or {}
    config = pipeline.get("config") if isinstance(pipeline.get("config"), dict) else {}
    quality = config.get("quality") or ticket.get("quality")
    command = [sys.executable, "-m", "vitrine", "--run-dir", str(run_dir)]
    if isinstance(quality, str) and quality.strip():
        command.extend(["--quality", quality.strip()])
    command.extend(["run"])
    if resume:
        command.append("--resume")
    source = run_dir / "source"
    command.extend(["--source", str(source), "--originals", str(source)])
    title = ticket.get("title") or config.get("title")
    subject = ticket.get("subject") or config.get("subject")
    if isinstance(title, str) and title.strip():
        command.extend(["--title", title.strip()])
    if isinstance(subject, str) and subject.strip():
        command.extend(["--subject", subject.strip()])
    capture_type = ticket.get("capture_type") or config.get("capture_type")
    if capture_type in ("scene", "object"):
        command.extend(["--capture-type", capture_type])
    return command


def _launch_capture(run_dir: Path, command: list[str], project_root: Path) -> subprocess.Popen:
    """Start a pipeline child with a persistent log and Windows-safe flags."""
    run_dir = Path(run_dir)
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "capture.log"
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with log_path.open("ab") as capture_log:
        # Keep the descriptor open in the child; Popen duplicates it on both
        # Windows and POSIX before this context closes.
        return subprocess.Popen(
            command,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=capture_log,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )


def _mtime_age_seconds(path: Path) -> float | None:
    """Seconds since *path* was last written, or None if it is missing."""
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _file_info(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    return {
        "name": path.name,
        "path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
        "bytes": st.st_size,
        "mtime": int(st.st_mtime),
    }


def _stage_status(run_dir: Path) -> dict[str, Any]:
    """Derive stage completion from artefacts that actually exist."""
    ingest_json = run_dir / "ingest" / "ingest.json"
    sfm_json = run_dir / "sfm" / "sfm.json"
    sparse_text = run_dir / "sfm" / "sparse_text"
    train_json = run_dir / "model" / "train.json"
    progress_json = run_dir / "model" / "progress.json"
    scene_ply = run_dir / "model" / "scene.ply"
    scene_splat = run_dir / "model" / "scene.splat"
    evaluation = run_dir / "model" / "evaluation.json"
    archive = run_dir / "archive" / "manifest.json"
    view_html = run_dir / "view" / "index.html"

    ingest = _read_json(ingest_json)
    sfm = _read_json(sfm_json)
    train = _read_json(train_json)
    # train.json only appears once training finishes; progress.json is its
    # in-progress cousin, written every ~500 steps — see train._write_progress.
    # Presence alone is not enough: a killed process leaves the file behind.
    progress = _read_json(progress_json) if train is None else None
    progress_age = _mtime_age_seconds(progress_json) if progress is not None else None
    train_running = (
        progress is not None
        and progress_age is not None
        and progress_age <= PROGRESS_FRESH_SECONDS
    )
    train_interrupted = progress is not None and not train_running
    eval_report = _read_json(evaluation)
    cleanup = run_dir / "model" / "scene.cleaned.ply"
    pipeline = _pipeline_record(run_dir)

    stages = {
        "ingest": {
            "done": ingest is not None,
            "report": ingest,
            "images_dir": (run_dir / "ingest" / "images").is_dir(),
        },
        "sfm": {
            "done": sfm is not None or (sparse_text / "images.txt").is_file()
            or (run_dir / "sfm" / "sparse" / "0" / "images.bin").is_file(),
            "report": sfm,
            "has_text_model": (sparse_text / "images.txt").is_file(),
            "has_bin_model": (run_dir / "sfm" / "sparse" / "0" / "images.bin").is_file(),
        },
        "train": {
            "done": train is not None or scene_ply.is_file(),
            "running": train_running,
            "interrupted": train_interrupted,
            "report": train or progress,
            "has_ply": scene_ply.is_file(),
            "has_splat": scene_splat.is_file(),
            "has_checkpoint": (run_dir / "model" / "checkpoint_10000.ply").is_file(),
            "progress_age_seconds": round(progress_age) if progress_age is not None else None,
        },
        "cleanup": {
            "done": cleanup.is_file(),
            "has_ply": cleanup.is_file(),
            "report": None,
        },
        "export": {
            "done": scene_splat.is_file(),
            "has_splat": scene_splat.is_file(),
            "report": _read_json(run_dir / "model" / "export.json"),
        },
        "evaluate": {
            "done": eval_report is not None,
            "report": eval_report,
        },
        "package": {
            "done": archive.is_file(),
            "report": _read_json(archive),
        },
        "view": {
            "done": view_html.is_file() or scene_splat.is_file(),
            "has_viewer": view_html.is_file(),
            "has_splat": scene_splat.is_file(),
        },
    }

    # The durable pipeline record is authoritative for stage state while a
    # run is active or has failed. The artefact-derived fields above remain
    # useful for older runs that predate pipeline.json.
    pipeline_stages = pipeline.get("stages") if isinstance(pipeline, dict) else None
    if isinstance(pipeline_stages, dict) and pipeline_stages:
        for name, record in pipeline_stages.items():
            if not isinstance(record, dict):
                continue
            stage = stages.setdefault(name, {})
            state = record.get("state")
            if isinstance(state, str):
                stage["pipeline_state"] = state
                stage["state"] = state
                stage["running"] = state == "running"
                stage["interrupted"] = state == "cancelled"
                stage["failed"] = state == "failed"
                if state == "complete":
                    stage["done"] = True
                if state in {"failed", "cancelled"}:
                    stage["error"] = record.get("error")
            for key in ("seconds", "started", "error", "outputs"):
                if key in record:
                    stage[key] = record[key]
        order = tuple(name for name in pipeline_stages if name in stages)
        # Keep read-only dashboard stages which are absent from a newer record.
        order += tuple(name for name in ("evaluate", "view") if name not in order)
    else:
        order = ("ingest", "sfm", "train", "evaluate", "export", "package", "view")
    done_count = sum(1 for k in order if stages[k]["done"])
    return {
        "stages": stages,
        "progress": {"done": done_count, "total": len(order), "order": list(order)},
    }


def _list_image_samples(run_dir: Path, limit: int = 24) -> list[dict[str, str]]:
    images_root = run_dir / "ingest" / "images"
    if not images_root.is_dir():
        return []
    samples: list[dict[str, str]] = []
    for group_dir in sorted(p for p in images_root.iterdir() if p.is_dir()):
        files = sorted(
            p for p in group_dir.iterdir()
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        )
        # Prefer a spread across the group, not just the first N.
        if len(files) <= 4:
            pick = files
        else:
            step = max(1, len(files) // 4)
            pick = files[::step][:4]
        for f in pick:
            rel = f.relative_to(run_dir).as_posix()
            samples.append({
                "group": group_dir.name,
                "name": f.name,
                "url": f"/files/{run_dir.name}/{rel}",
            })
            if len(samples) >= limit:
                return samples
    return samples


def _objects_summary(run_dir: Path) -> dict[str, Any] | None:
    """Read-only summary of the object sidecar's output, or ``None`` if absent.

    Count, labels and a turntable thumbnail per object — enough for a small
    dashboard panel without the server needing to understand the sidecar.
    """
    manifest = run_dir / "objects" / "objects.json"
    doc = _read_json(manifest)
    if not doc:
        return None
    from .objects import safe_component

    name = run_dir.name
    records = doc.get("objects") if isinstance(doc, dict) else None
    if not isinstance(records, list):
        return None
    items: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        oid = rec.get("object_id")
        # A thumbnail is offered only for a safe, contained object id — never
        # build a served path from an untrusted identifier.
        thumb_url = None
        if safe_component(oid):
            thumb_rel = f"objects/{oid}/preview.png"
            if not _safe_file_under_run(run_dir, thumb_rel):
                thumb_rel = f"objects/{oid}/turntable/view_00.png"
            candidate = (run_dir / thumb_rel).resolve()
            if candidate.is_relative_to(run_dir.resolve()) and candidate.is_file():
                thumb_url = f"/files/{quote(name)}/{quote(thumb_rel)}"
        items.append({
            "object_id": oid if safe_component(oid) else None,
            "label": rec.get("label") if isinstance(rec.get("label"), str) else None,
            "confidence": rec.get("confidence"),
            "coverage": rec.get("coverage"),
            "asset_type": "gaussian-splat" if rec.get("splat_path") else "mesh",
            "splat_url": (
                f"/files/{quote(name)}/objects/{quote(rec['splat_path'])}"
                if isinstance(rec.get("splat_path"), str)
                and rec["splat_path"].lower().endswith(".splat")
                and _safe_file_under_run(run_dir, f"objects/{rec['splat_path']}") is not None
                else None
            ),
            "viewer_url": (
                f"/viewer/{quote(name)}?scene={quote('objects/' + rec['splat_path'], safe='')}&label={quote(str(rec.get('label', 'Object')))}"
                if isinstance(rec.get("splat_path"), str)
                and rec["splat_path"].lower().endswith(".splat")
                and _safe_file_under_run(run_dir, f"objects/{rec['splat_path']}") is not None
                else None
            ),
            "thumb_url": thumb_url,
            "thumb_kind": "source-crop" if thumb_url and thumb_rel.endswith("/preview.png") else "render",
            "mesh_url": (
                f"/files/{quote(name)}/objects/{quote(rec['mesh_path'])}"
                if isinstance(rec.get("mesh_path"), str)
                and _safe_file_under_run(run_dir, f"objects/{rec['mesh_path']}") is not None
                else None
            ),
            "mesh_name": Path(rec["mesh_path"]).name
            if isinstance(rec.get("mesh_path"), str) else None,
        })
    summary: dict[str, Any] = {"count": len(items), "objects": items}
    # optional composed-scene glb (all objects placed in one glTF scene)
    cs = doc.get("composed_scene") if isinstance(doc, dict) else None
    if isinstance(cs, dict) and safe_component(str(cs.get("path", "")).split("/")[0]):
        rel = cs["path"]
        if isinstance(rel, str) and _safe_file_under_run(run_dir, f"objects/{rel}") is not None:
            summary["composed_scene"] = {
                "url": f"/files/{quote(name)}/objects/{quote(rel)}",
                "sha256": cs.get("sha256"),
                "derivative_class": "composed-derivative",
            }
    return summary


def _object_workflow(run_dir: Path, process: subprocess.Popen | None = None) -> dict[str, Any]:
    """Truthful UI state for the optional external object-separation stage."""
    model_files = []
    for role, rel in (
        ("Gaussian master", "model/scene.ply"),
        ("Web splat", "model/scene.splat"),
        ("Fused mesh", "model/mesh.ply"),
        ("Fused mesh", "model/mesh.obj"),
        ("Fused mesh", "model/mesh.glb"),
    ):
        info = _file_info(run_dir / rel)
        if info:
            model_files.append({
                "role": role,
                **info,
                "url": f"/files/{quote(run_dir.name)}/{quote(rel)}",
            })
    running = process is not None and process.poll() is None
    return {
        "configured": bool(os.environ.get("VITRINE_OBJECT_SIDECAR")),
        "ready": bool(model_files),
        "running": running,
        "returncode": None if process is None or running else process.returncode,
        "inputs": model_files,
        "outputs": _objects_summary(run_dir),
        "log_url": f"/api/runs/{quote(run_dir.name)}/log?which=objects",
    }


def _summarise_run(run_dir: Path) -> dict[str, Any]:
    name = run_dir.name
    samples = _list_image_samples(run_dir, limit=1)
    # A reviewed registered viewpoint also provides an intentional library cover.
    try:
        viewpoints = json.loads((run_dir / "model" / "viewer-cameras.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        viewpoints = None
    if isinstance(viewpoints, list) and viewpoints and isinstance(viewpoints[0], dict):
        image_name = viewpoints[0].get("name")
        if isinstance(image_name, str):
            relative = f"ingest/images/{image_name}"
            if _safe_file_under_run(run_dir, relative):
                samples = [{"name": Path(image_name).name,
                            "group": str(Path(image_name).parent),
                            "url": f"/files/{quote(run_dir.name)}/{quote(relative, safe='/')}"}]
    manifest = _read_json(run_dir / "archive" / "manifest.json") or {}
    capture = _read_json(run_dir / "capture.json") or {}
    label = _read_json(run_dir / "run-label.json") or {}
    title = label.get("title") or manifest.get("title") or capture.get("title")
    # A cover is a render of this model, never a source photograph presented as one.
    hero = _safe_file_under_run(run_dir, "model/library-hero.jpg")
    hero_record = _read_json(run_dir / "model" / "library-hero.json") or {}
    model_path = run_dir / "model" / "scene.ply"
    if hero and model_path.is_file() and hero_record.get("source_mtime_ns") == model_path.stat().st_mtime_ns:
        samples = [{"name": "Splat preview", "kind": "splat-render",
                    "url": f"/files/{quote(name)}/model/library-hero.jpg?v={hero.stat().st_mtime_ns}"}]
    status = _stage_status(run_dir)
    pipeline = _pipeline_record(run_dir)
    train = status["stages"]["train"]["report"] or {}
    ingest = status["stages"]["ingest"]["report"] or {}
    sfm = status["stages"]["sfm"]["report"] or {}
    from .train import electricity_rate_gbp_per_kwh

    electricity_rate = electricity_rate_gbp_per_kwh()
    energy_kwh = train.get("energy_kwh")
    current_cost_gbp = (
        round(float(energy_kwh) * electricity_rate, 2)
        if energy_kwh is not None
        else train.get("cost_gbp")
    )

    artefacts = {
        "scene_ply": _file_info(run_dir / "model" / "scene.ply"),
        "scene_splat": _file_info(run_dir / "model" / "scene.splat"),
        "checkpoint": _file_info(run_dir / "model" / "checkpoint_10000.ply"),
        "train_json": _file_info(run_dir / "model" / "train.json"),
        "ingest_json": _file_info(run_dir / "ingest" / "ingest.json"),
        "sfm_json": _file_info(run_dir / "sfm" / "sfm.json"),
        "sfm_log": _file_info(run_dir / "logs" / "vitrine.log") or _file_info(run_dir / "logs" / "sfm.log")
        or _file_info(run_dir / "sfm" / "colmap.log"),
        "train_log": _file_info(run_dir / "logs" / "vitrine.log") or _file_info(run_dir / "logs" / "train-standard.log")
        or _file_info(run_dir / "model" / "training.log"),
    }

    # Export time is the available creation record for legacy models. Unlike
    # directory/label mtimes, it is unchanged by rename, previews or packaging.
    model_info = artefacts["scene_ply"] or artefacts["scene_splat"]
    splat_created_mtime = model_info["mtime"] if model_info else None

    pipeline_state = pipeline.get("state") if isinstance(pipeline, dict) else None
    pipeline_active = pipeline_state in {"running", "queued"}
    running = pipeline_active or status["stages"]["train"].get("running", False)
    interrupted = (
        pipeline_state in {"failed", "cancelled"}
        or status["stages"]["train"].get("interrupted", False)
    ) and not running
    # progress.json (running=True, or a stale interrupted mid-run) has no
    # final_psnr/minutes/peak_vram_gb — those only exist on train.json. Fall
    # back to the most recent periodic eval in its history so headline cards
    # aren't blank for live *or* interrupted runs.
    last_eval = (train.get("history") or [{}])[-1] if (running or interrupted) else {}
    evaluation = (status["stages"]["evaluate"]["report"] or {}) if not (running or interrupted) else {}
    metric_source = "evaluation" if evaluation.get("overall_psnr") is not None else (
        "export" if train.get("export_psnr") is not None else "training"
    )

    # Best "last activity" stamp for the library list: prefer final model
    # artefacts, then stage reports, then the run directory itself.
    mtimes = [
        info["mtime"]
        for info in artefacts.values()
        if info and info.get("mtime")
    ]
    progress_json = run_dir / "model" / "progress.json"
    progress_age = _mtime_age_seconds(progress_json)
    if progress_age is not None:
        try:
            mtimes.append(int(progress_json.stat().st_mtime))
        except OSError:
            pass
    try:
        mtimes.append(int(run_dir.stat().st_mtime))
    except OSError:
        pass
    updated_mtime = max(mtimes) if mtimes else None
    updated_at = (
        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(updated_mtime))
        if updated_mtime is not None
        else None
    )

    return {
        "name": name,
        "title": title if isinstance(title, str) and title.strip() else None,
        "capture_type": capture.get("capture_type", manifest.get("capture_type", "scene")),
        "preview": samples[0] if samples else None,
        "path": str(run_dir.relative_to(PROJECT_ROOT)) if run_dir.is_relative_to(PROJECT_ROOT) else str(run_dir),
        "updated_mtime": updated_mtime,
        "updated_at": updated_at,
        "splat_created_mtime": splat_created_mtime,
        "progress": status["progress"],
        "stages": status["stages"],
        "headline": {
            "accepted_images": ingest.get("accepted"),
            "rejected_images": ingest.get("rejected"),
            "groups": len(ingest.get("groups") or []),
            "registered_images": sfm.get("registered_images"),
            "cameras": sfm.get("cameras"),
            "points": sfm.get("points"),
            "profile": train.get("profile"),
            "psnr": evaluation.get("overall_psnr", train.get("export_psnr", train.get("final_psnr", last_eval.get("psnr")))),
            "ssim": evaluation.get("overall_ssim", train.get("export_ssim", train.get("final_ssim", last_eval.get("ssim")))),
            "metric_source": metric_source,
            "n_gaussians": train.get("n_gaussians"),
            "minutes": train.get("minutes", train.get("elapsed_minutes")),
            "peak_vram_gb": train.get("peak_vram_gb"),
            "iterations": train.get("iterations"),
            "running": running,
            "interrupted": interrupted,
            "pipeline_state": pipeline_state,
            "pipeline_error": pipeline.get("error") if isinstance(pipeline, dict) else None,
            "step": train.get("step") if (running or interrupted) else None,
            "eta_minutes": train.get("eta_minutes") if running else None,
            "energy_kwh": energy_kwh,
            "cost_gbp": current_cost_gbp,
            "electricity_rate_gbp_per_kwh": electricity_rate,
        },
        "artefacts": artefacts,
        "objects": _objects_summary(run_dir),
        "pipeline": pipeline,
        "has_viewer": bool(artefacts["scene_splat"]),
        "viewer_url": f"/viewer/{name}" if artefacts["scene_splat"] else None,
        "splat_url": f"/files/{name}/model/scene.splat" if artefacts["scene_splat"] else None,
    }


def _list_runs(
    runs_root: Path,
    *,
    only: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Summarise run directories under *runs_root*.

    If *only* is set, hide every other folder name from the library list.
    Diagnostic / failed experiment dirs stay on disk; they just leave the UI.
    """
    if not runs_root.is_dir():
        return []
    runs: list[dict[str, Any]] = []
    for path in sorted(runs_root.iterdir()):
        if not path.is_dir() or path.name.startswith("."):
            continue
        if only is not None and path.name not in only:
            continue
        # Skip non-run directories (logs dropped at root of runs/, etc.)
        markers = (
            path / "ingest",
            path / "sfm",
            path / "model",
            path / "archive",
            path / "capture.json",
        )
        if not any(m.exists() for m in markers):
            continue
        summary = _summarise_run(path)
        summary["experiments"] = []
        experiments = path / "experiments"
        if experiments.is_dir() and not experiments.is_symlink():
            from .construction import construction_payload
            for experiment in sorted(experiments.iterdir()):
                if not experiment.is_dir() or experiment.is_symlink():
                    continue
                model = experiment / "model" if (experiment / "model").is_dir() else experiment
                if model.is_symlink():
                    continue
                if not any((model / marker).exists() for marker in ("scene.ply", "progress.json", "construction-status.json")):
                    continue
                payload = construction_payload(path, folder=experiment)
                images = payload.get("images") or []
                complete = (model / "scene.ply").is_file()
                summary["experiments"].append({
                    "name": experiment.name,
                    "state": payload.get("state", "unknown"),
                    "complete": complete,
                    "preview": images[-1]["url"] if images else None,
                    "url": "/static/construction.html?run=" + quote(path.name, safe="") + "&experiment=" + quote(experiment.name, safe=""),
                    "ply_url": "/files/" + quote(path.name, safe="") + "/" + quote((model / "scene.ply").relative_to(path).as_posix(), safe="/") if complete else None,
                })
        runs.append(summary)
    # Most recently touched first (by train.json or dir mtime).
    def sort_key(r: dict[str, Any]) -> float:
        if r.get("splat_created_mtime") is not None:
            return float(r["splat_created_mtime"])
        art = r.get("artefacts") or {}
        for key in ("train_json", "scene_ply", "ingest_json", "checkpoint"):
            info = art.get(key)
            if info and info.get("mtime"):
                return float(info["mtime"])
        # Live / interrupted trains only have progress.json — still sort by it.
        progress = runs_root / r["name"] / "model" / "progress.json"
        try:
            return float(progress.stat().st_mtime)
        except OSError:
            return 0.0

    runs.sort(key=sort_key, reverse=True)
    return runs


def _doctor_payload() -> dict[str, Any]:
    from . import cuda_toolkit

    status = cuda_toolkit.status()
    gpu: dict[str, Any] = {"available": False}
    try:
        import torch

        if torch.cuda.is_available():
            props = torch.cuda.get_device_properties(0)
            gpu = {
                "available": True,
                "name": torch.cuda.get_device_name(0),
                "vram_gb": round(props.total_memory / 2**30, 2),
                "capability": f"{props.major}.{props.minor}",
            }
    except Exception as exc:  # noqa: BLE001
        gpu = {"available": False, "error": str(exc)}

    tier = profiles.detect_tier()
    docker = shutil.which("docker")
    ffmpeg = shutil.which("ffmpeg")
    from .windows_tools import docker_engine_ready
    docker_engine = docker_engine_ready()
    docker_gpu: bool | None = None
    if docker_engine:
        try:
            from .sfm import gpu_available

            docker_gpu = bool(gpu_available())
        except Exception:  # noqa: BLE001
            docker_gpu = False

    ok = bool(
        status.get("cuda_root")
        and status.get("host_compiler")
        and gpu.get("available")
        and docker_engine
        and ffmpeg
    )

    return {
        "ok": ok,
        "version": __version__,
        "cuda": status,
        "gpu": gpu,
        "tier": tier,
        "tools": {
            "docker": docker,
            "ffmpeg": ffmpeg,
            "docker_gpu": docker_gpu,
            "docker_engine": docker_engine,
        },
        "profile_preview": profiles.describe(profiles.resolve("standard", tier)),
    }


def _profiles_payload() -> list[dict[str, Any]]:
    rows = []
    for tier in profiles.TIERS:
        for quality in profiles.QUALITY_LEVELS:
            p = profiles.resolve(quality, tier)
            minutes = p.estimated_minutes()
            rows.append({
                **profiles.describe(p),
                "estimated_minutes": round(minutes, 1) if minutes is not None else None,
                "tier": tier,
                "quality": quality,
            })
    return rows


def _safe_run_dir(runs_root: Path, name: str) -> Path | None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        return None
    path = (runs_root / name).resolve()
    try:
        path.relative_to(runs_root.resolve())
    except ValueError:
        return None
    return path if path.is_dir() else None


def _safe_file_under_run(run_dir: Path, rel: str) -> Path | None:
    rel = unquote(rel).lstrip("/")
    if not rel or ".." in Path(rel).parts:
        return None
    path = (run_dir / rel).resolve()
    try:
        path.relative_to(run_dir.resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


class VitrineHandler(SimpleHTTPRequestHandler):
    """Serve the dashboard, JSON APIs, run files, and splat viewer."""

    # Set on the class by ``serve()``.
    project_root: Path = PROJECT_ROOT
    runs_root: Path = PROJECT_ROOT / "runs"
    ui_dir: Path = UI_DIR
    # When set, only these run directory names appear in the library / detail APIs.
    # Disk is untouched — diagnostic runs stay under runs/.
    run_allowlist: set[str] | None = None
    upload_lock = threading.Lock()
    object_lock = threading.Lock()
    management_lock = threading.Lock()
    object_processes: dict[str, subprocess.Popen] = {}
    capture_processes: dict[str, subprocess.Popen] = {}
    mesh_processes: dict[str, subprocess.Popen] = {}

    def _with_capture_job(self, run: dict[str, Any]) -> dict[str, Any]:
        pipeline = run.get("pipeline") or {}
        process = self.capture_processes.get(run["name"])
        if process is not None:
            code = process.poll()
            job = {"running": code is None, "returncode": code, "process_id": process.pid}
            if code is not None and code != 0:
                if pipeline.get("state") not in {"complete", "completed", "failed", "cancelled"}:
                    pipeline = dict(pipeline)
                    pipeline["state"] = "failed"
                    pipeline.setdefault("error", f"pipeline exited with code {code}")
                    run["pipeline"] = pipeline
                job["error"] = pipeline.get("error")
            run["capture_job"] = job
        elif pipeline.get("state") in {"running", "queued"}:
            # A dashboard restart loses the in-memory Popen handle. Use the
            # persisted PID as a hint, while explicitly exposing stale state
            # so the UI can offer Resume rather than showing a false spinner.
            pid = pipeline.get("pid")
            active = _pid_is_running(pid)
            run["capture_job"] = {
                "running": active,
                "returncode": None,
                "process_id": pid if isinstance(pid, int) else None,
                "stale": not active,
                "error": None if active else "The saved pipeline is no longer running; resume it to continue.",
            }
        return run

    def _visible(self, name: str) -> bool:
        if self.run_allowlist is None:
            return True
        return name in self.run_allowlist

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, *, download_name: str | None = None) -> None:
        ctype, _ = mimetypes.guess_type(str(path))
        if ctype is None:
            ctype = "application/octet-stream"
        # .splat is not in the standard map
        if path.suffix.lower() == ".splat":
            ctype = "application/octet-stream"
        try:
            size = path.stat().st_size
            fh = path.open("rb")
        except OSError as exc:
            self._send_text(f"cannot read file: {exc}", status=500)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "public, max-age=60")
            if download_name:
                self.send_header("Content-Disposition", f'inline; filename="{download_name}"')
            self.end_headers()
            # Stream so multi-hundred-MB PLYs don't sit in RAM.
            shutil.copyfileobj(fh, self.wfile, length=1024 * 1024)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass
        finally:
            fh.close()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path in {"/", "/index.html"}:
            return self._send_file(self.ui_dir / "index.html")

        if path.startswith("/static/"):
            rel = path[len("/static/") :]
            if ".." in Path(rel).parts:
                return self._send_text("bad path", status=400)
            file_path = (self.ui_dir / rel).resolve()
            try:
                file_path.relative_to(self.ui_dir.resolve())
            except ValueError:
                return self._send_text("bad path", status=400)
            if not file_path.is_file():
                return self._send_text("not found", status=404)
            return self._send_file(file_path)

        if path == "/api/health":
            return self._send_json({"ok": True, "version": __version__})

        if path == "/api/doctor":
            try:
                return self._send_json(_doctor_payload())
            except Exception as exc:  # noqa: BLE001
                return self._send_json({"ok": False, "error": str(exc)}, status=500)

        if path == "/api/profiles":
            return self._send_json({
                "detected_tier": profiles.detect_tier(),
                "profiles": _profiles_payload(),
            })

        if path == "/api/preflight":
            query = parse_qs(parsed.query)
            requested = (query.get("run") or [None])[0]
            full = (query.get("full") or query.get("quick") or ["0"])[0].lower() in {"1", "true", "yes", "full"}
            run_dir = None
            if requested:
                requested = unquote(str(requested)).strip()
                if not self._visible(requested):
                    return self._send_json({"error": "run not found"}, status=404)
                run_dir = _safe_run_dir(self.runs_root, requested)
                if run_dir is None:
                    return self._send_json({"error": "run not found"}, status=404)
            source = run_dir / "source" if run_dir is not None and (run_dir / "source").exists() else None
            report = _preflight_payload(
                source=source,
                output=run_dir or self.runs_root,
                full=full,
            )
            if requested:
                report["run"] = requested
            return self._send_json(report)

        if path == "/api/construction":
            from .live_build import activity
            return self._send_json({"jobs": [j for j in activity(self.runs_root)["jobs"] if self._visible(j["run"])]})

        if path.startswith("/api/construction-image/"):
            from .live_build import construction_image
            image = construction_image(self.runs_root, path[len("/api/construction-image/"):])
            if image is None:
                return self._send_json({"error": "snapshot not found"}, status=404)
            return self._send_file(image)

        if path == "/api/trash":
            root = self.runs_root.resolve()
            trash = (root / ".trash").resolve()
            records = []
            if trash.parent == root and trash.is_dir():
                for folder in sorted(trash.iterdir()):
                    name, separator, token = folder.name.rpartition("--")
                    if folder.is_dir() and not folder.is_symlink() and separator and re.fullmatch(r"[0-9a-f]{32}", token):
                        label = _read_json(folder / "run-label.json") or _read_json(folder / "capture.json") or {}
                        records.append({"id": folder.name, "name": name, "title": label.get("title") or name})
            return self._send_json({"runs": records})
        if path == "/api/runs":
            return self._send_json({
                "runs": [self._with_capture_job(run) for run in _list_runs(self.runs_root, only=self.run_allowlist)],
                "filter": sorted(self.run_allowlist) if self.run_allowlist else None,
            })

        if path.startswith("/api/runs/"):
            rest = path[len("/api/runs/") :].strip("/")
            parts = rest.split("/")
            name = parts[0]
            if not self._visible(name):
                return self._send_json({"error": "run not found"}, status=404)
            run_dir = _safe_run_dir(self.runs_root, name)
            if run_dir is None:
                return self._send_json({"error": "run not found"}, status=404)
            if len(parts) == 1:
                detail = self._with_capture_job(_summarise_run(run_dir))
                detail["object_workflow"] = _object_workflow(
                    run_dir, self.object_processes.get(name)
                )
                from .object_mesh import mesh_summary
                detail["object_meshes"] = mesh_summary(run_dir, self.mesh_processes.get(name))
                detail["samples"] = _list_image_samples(run_dir)
                # Include full reports for the detail pane (already in stages).
                return self._send_json(detail)
            if len(parts) == 2 and parts[1] == "summary":
                return self._send_json(self._with_capture_job(_summarise_run(run_dir)))
            if len(parts) == 2 and parts[1] == "preflight":
                query = parse_qs(parsed.query)
                full = (query.get("full") or ["0"])[0].lower() in {"1", "true", "yes", "full"}
                source = run_dir / "source" if (run_dir / "source").exists() else None
                report = _preflight_payload(source=source, output=run_dir, full=full)
                report["run"] = name
                return self._send_json(report)
            if len(parts) == 2 and parts[1] == "construction":
                from .construction import construction_payload
                experiment = (parse_qs(parsed.query).get("experiment") or [None])[0]
                folder = None
                if experiment:
                    folder = run_dir / "experiments" / experiment
                    if (Path(experiment).name != experiment or experiment in (".", "..")
                            or not folder.is_dir() or folder.is_symlink()
                            or not folder.resolve().is_relative_to(run_dir.resolve())
                            or folder.resolve().parent != (run_dir / "experiments").resolve()):
                        return self._send_json({"error": "experiment not found"}, status=404)
                return self._send_json(construction_payload(
                    run_dir, None if folder else self.capture_processes.get(name), folder))
            if len(parts) == 2 and parts[1] == "log":
                qs = parse_qs(parsed.query)
                which = (qs.get("which") or ["train"])[0]
                candidates = {
                    "train": [
                        run_dir / "capture.log",
                        run_dir / "model" / "training.log",
                        run_dir / "logs" / "vitrine.log",
                        run_dir / "logs" / "train-standard.log",
                        run_dir / "logs" / "train.log",
                    ],
                    "sfm": [
                        run_dir / "capture.log",
                        run_dir / "logs" / "vitrine.log",
                        run_dir / "logs" / "sfm.log",
                        run_dir / "sfm" / "colmap.log",
                    ],
                    "objects": [run_dir / "logs" / "objects.log"],
                }.get(which, [])
                for cand in candidates:
                    if cand.is_file():
                        # Tail last ~80 KB so the UI stays snappy.
                        raw = cand.read_bytes()
                        tail = raw[-80_000:] if len(raw) > 80_000 else raw
                        text = tail.decode("utf-8", errors="replace")
                        return self._send_json({
                            "which": which,
                            "path": str(cand.relative_to(self.project_root))
                            if cand.is_relative_to(self.project_root)
                            else str(cand),
                            "bytes": len(raw),
                            "tail": text,
                        })
                return self._send_json({"error": f"no {which} log found"}, status=404)
            if len(parts) == 2 and parts[1] == "objects":
                process = self.object_processes.get(name)
                return self._send_json(_object_workflow(run_dir, process))
            return self._send_json({"error": "unknown endpoint"}, status=404)

        if path.startswith("/files/"):
            rest = path[len("/files/") :].lstrip("/")
            if "/" not in rest:
                return self._send_text("bad path", status=400)
            name, rel = rest.split("/", 1)
            run_dir = _safe_run_dir(self.runs_root, name)
            if run_dir is None:
                return self._send_text("run not found", status=404)
            file_path = _safe_file_under_run(run_dir, rel)
            if file_path is None:
                return self._send_text("file not found", status=404)
            return self._send_file(file_path, download_name=file_path.name)

        if path.startswith("/viewer/"):
            name = path[len("/viewer/") :].strip("/").split("/")[0]
            run_dir = _safe_run_dir(self.runs_root, name)
            if run_dir is None:
                return self._send_text("run not found", status=404)
            requested_scene = parse_qs(urlparse(self.path).query).get("scene", ["model/scene.splat"])[0]
            scene_file = _safe_file_under_run(run_dir, requested_scene)
            if scene_file is None or scene_file.suffix != ".splat" or not scene_file.is_file():
                return self._send_text("no scene.splat for this run", status=404)
            return self._send_file(self.ui_dir / "viewer.html")

        return self._send_text("not found", status=404)

    def do_POST(self) -> None:  # noqa: N802
        """Accept local capture media and start the existing CLI pipeline."""
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        control = re.fullmatch(r"/api/runs/([^/]+)/(resume|cancel|open-folder)", path)
        if control:
            name, action = control[1], control[2]
            run_dir = _safe_run_dir(self.runs_root, name) if self._visible(name) else None
            if run_dir is None:
                return self._send_json({"error": "run not found"}, status=404)
            if self.headers.get("Origin") and urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                return self._send_json({"error": "local workspace request required"}, status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096:
                    raise ValueError("Expected a small JSON request")
                json.loads(self.rfile.read(length))
                from .construction import atomic_json, read_json
                with self.management_lock:
                    state = read_json(run_dir / "pipeline.json")
                    process = self.capture_processes.get(name)
                    active = process is not None and process.poll() is None
                    if action == "open-folder":
                        if os.name != "nt":
                            raise ValueError("Open folder is available on Windows")
                        os.startfile(str(run_dir))
                        return self._send_json({"ok": True})
                    if not state:
                        raise ValueError("Historical run has no pipeline record; use individual CLI stages")
                    if action == "cancel":
                        if state.get("state") != "running":
                            raise ValueError("This pipeline is not running")
                        atomic_json(run_dir / "cancel.request", {"requested": time.time()})
                        return self._send_json({"ok": True, "message": "Cancellation requested; completed stages are retained"})
                    if active:
                        raise ValueError("This capture is already running")
                    from .pipeline import run_lock
                    with run_lock(run_dir):
                        pass
                    with (run_dir / "capture.log").open("ab") as log:
                        process = subprocess.Popen(_capture_command(run_dir, resume=True), cwd=self.project_root,
                            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    self.capture_processes[name] = process
                    return self._send_json({"ok": True, "process_id": process.pid}, status=202)
            except (OSError, ValueError, RuntimeError) as exc:
                return self._send_json({"error": str(exc)}, status=409)
        if path.startswith("/api/runs/") and path.rsplit("/", 1)[-1] in {"rename", "trash"}:
            action = path.rsplit("/", 1)[-1]
            name = path[len("/api/runs/"):].rsplit("/", 1)[0]
            if not self._visible(name):
                return self._send_json({"error": "run not found"}, status=404)
            run_dir = _safe_run_dir(self.runs_root, name)
            root = self.runs_root.resolve()
            if run_dir is None or run_dir.parent != root or name.startswith(".") or (self.runs_root / name).is_symlink():
                return self._send_json({"error": "run not found"}, status=404)
            if self.headers.get("Origin") and urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                return self._send_json({"error": "local workspace request required"}, status=403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected a small JSON request")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                with self.management_lock:
                    if action == "rename":
                        title = body.get("title")
                        if not isinstance(title, str) or not title.strip() or len(title) > 160 or any(ord(c) < 32 for c in title):
                            raise ValueError("Choose a name between 1 and 160 characters")
                        temporary = run_dir / (".run-label-" + uuid.uuid4().hex + ".json")
                        temporary.write_text(json.dumps({"title": title.strip()}, ensure_ascii=False), encoding="utf-8")
                        os.replace(temporary, run_dir / "run-label.json")
                        return self._send_json({"ok": True, "title": title.strip()})
                    if body.get("confirm_name") != name:
                        raise ValueError("Confirm the exact capture before removing it")
                    processes = [self.capture_processes.get(name), self.object_processes.get(name), self.mesh_processes.get(name)]
                    if any(p is not None and p.poll() is None for p in processes) or _stage_status(run_dir)["stages"]["train"]["running"]:
                        return self._send_json({"error": "This capture is processing. Wait for it to finish before removing it."}, status=409)
                    trash = (root / ".trash").resolve()
                    if trash.parent != root:
                        raise ValueError("Trash must remain inside the runs folder")
                    trash.mkdir(exist_ok=True)
                    destination = trash / (name + "--" + uuid.uuid4().hex)
                    # Both resolved paths are constrained to this workspace's runs folder.
                    os.replace(run_dir, destination)
                    return self._send_json({"ok": True, "trash_id": destination.name, "original_name": name})
            except (ValueError, OSError) as exc:
                return self._send_json({"error": str(exc)}, status=400)
        if path.startswith("/api/trash/") and path.endswith("/restore"):
            trash_id = path[len("/api/trash/"):-len("/restore")]
            root = self.runs_root.resolve()
            trash = (root / ".trash").resolve()
            source = _safe_run_dir(trash, trash_id)
            name, separator, token = trash_id.rpartition("--")
            if (trash.parent != root or source is None or source.parent != trash or not separator
                    or not re.fullmatch(r"[0-9a-f]{32}", token) or (trash / trash_id).is_symlink()):
                return self._send_json({"error": "removed capture not found"}, status=404)
            if self.headers.get("Origin") and urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                return self._send_json({"error": "local workspace request required"}, status=403)
            if self.headers.get_content_type() != "application/json":
                return self._send_json({"error": "JSON request required"}, status=400)
            destination = (root / name).resolve()
            if destination.parent != root or name.startswith("."):
                return self._send_json({"error": "invalid capture name"}, status=400)
            with self.management_lock:
                if destination.exists():
                    return self._send_json({"error": "A capture already uses that folder name. Nothing was overwritten."}, status=409)
                try:
                    os.replace(source, destination)
                    if self.run_allowlist is not None:
                        self.run_allowlist.add(name)
                    return self._send_json({"ok": True, "name": name})
                except OSError as exc:
                    return self._send_json({"error": str(exc)}, status=400)
        if path.startswith("/api/runs/") and path.endswith("/object-meshes"):
            name = path[len("/api/runs/"):-len("/object-meshes")].strip("/")
            run_dir = _safe_run_dir(self.runs_root, name) if self._visible(name) else None
            if run_dir is None:
                return self._send_json({"error": "run not found"}, status=404)
            with self.object_lock:
                if any(p is not None and p.poll() is None for p in
                       (self.object_processes.get(name), self.mesh_processes.get(name), self.capture_processes.get(name))):
                    return self._send_json({"error": "Wait for this capture's processing to finish"}, status=409)
                if not (run_dir / "objects" / "objects.json").is_file():
                    return self._send_json({"error": "Separate objects before creating meshes"}, status=409)
                if not (run_dir / "sfm" / "sparse_text" / "images.txt").is_file():
                    return self._send_json({"error": "Registered source cameras are required"}, status=409)
                logs = run_dir / "logs"
                logs.mkdir(exist_ok=True)
                try:
                    with (logs / "object-meshes.log").open("ab") as log:
                        self.mesh_processes[name] = subprocess.Popen(
                            [sys.executable, "-m", "vitrine", "--run-dir", str(run_dir), "object-meshes"],
                            cwd=self.project_root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
                except OSError as exc:
                    return self._send_json({"error": str(exc)}, status=500)
            return self._send_json({"ok": True}, status=202)
        if path.startswith("/api/runs/") and path.endswith("/objects"):
            name = path[len("/api/runs/") : -len("/objects")].strip("/")
            if not self._visible(name):
                return self._send_json({"error": "run not found"}, status=404)
            run_dir = _safe_run_dir(self.runs_root, name)
            if run_dir is None:
                return self._send_json({"error": "run not found"}, status=404)
            mesh_process = self.mesh_processes.get(name)
            if mesh_process is not None and mesh_process.poll() is None:
                return self._send_json({"error": "Mesh generation is running"}, status=409)
            workflow = _object_workflow(run_dir, self.object_processes.get(name))
            if workflow["running"]:
                return self._send_json({"error": "object separation is already running"}, status=409)
            if not workflow["configured"]:
                return self._send_json({
                    "error": "Object sidecar is not configured. Set VITRINE_OBJECT_SIDECAR before starting the dashboard."
                }, status=409)
            if not workflow["ready"]:
                return self._send_json({
                    "error": "No 3D model output found. Train or export this run first."
                }, status=409)
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            command = [
                sys.executable, "-m", "vitrine", "--run-dir", str(run_dir), "objects",
            ]
            try:
                sidecar_args = json.loads(os.environ.get("VITRINE_OBJECT_SIDECAR_ARGS_JSON", "[]"))
                if not isinstance(sidecar_args, list) or any(not isinstance(arg, str) for arg in sidecar_args):
                    raise ValueError("expected a JSON array of strings")
            except (json.JSONDecodeError, ValueError) as exc:
                return self._send_json({"error": f"Invalid sidecar argument configuration: {exc}"}, status=409)
            command.extend(f"--sidecar-arg={arg}" for arg in sidecar_args)
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            try:
                with self.object_lock, (logs_dir / "objects.log").open("ab") as log:
                    mesh_process = self.mesh_processes.get(name)
                    if mesh_process is not None and mesh_process.poll() is None:
                        return self._send_json({"error": "Mesh generation is running"}, status=409)
                    current = self.object_processes.get(name)
                    if current is not None and current.poll() is None:
                        return self._send_json({"error": "object separation is already running"}, status=409)
                    process = subprocess.Popen(
                        command,
                        cwd=self.project_root,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=creation_flags,
                    )
                    self.object_processes[name] = process
                return self._send_json({"ok": True, "process_id": process.pid}, status=202)
            except OSError as exc:
                logger.exception("object separation launch failed")
                return self._send_json({"error": str(exc)}, status=500)
        if parsed.path != "/api/captures":
            return self._send_json({"error": "unknown endpoint"}, status=404)

        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return self._send_json({"error": "multipart form data required"}, status=400)

        form: _MultipartForm | None = None
        try:
            form = _parse_multipart_form(self.rfile, self.headers)
            title = str(form.getfirst("title", "New capture")).strip() or "New capture"
            subject = str(form.getfirst("subject", "Not recorded.")).strip() or "Not recorded."
            capture_type = str(form.getfirst("capture_type", "scene")).strip().lower()
            if capture_type not in ("scene", "object"):
                return self._send_json({"error": "invalid capture type"}, status=400)
            quality = str(form.getfirst("quality", "standard")).strip().lower()
            if quality not in profiles.QUALITY_LEVELS:
                return self._send_json({"error": "invalid quality level"}, status=400)

            def _form_files(name: str) -> list:
                items = form[name] if name in form else []
                if not isinstance(items, list):
                    items = [items]
                return [item for item in items if getattr(item, "filename", None)]

            uploads = _form_files("files")
            session_uploads = _form_files("session")
            if session_uploads:
                return self._send_json(
                    {"error": "Vitrine App import is coming soon. Add photographs and video instead."},
                    status=501,
                )
            if not uploads:
                return self._send_json({"error": "choose at least one image or video"}, status=400)

            with self.upload_lock:
                base = _run_slug(title)
                name = base
                suffix = 2
                while (self.runs_root / name).exists():
                    name = f"{base}-{suffix}"
                    suffix += 1
                run_dir = self.runs_root / name
                source_dir = run_dir / "source"
                source_dir.mkdir(parents=True, exist_ok=False)

            saved: list[str] = []
            session_info: dict[str, Any] | None = None
            if session_uploads:
                from .capture_session import CaptureSessionError, import_session

                if len(session_uploads) != 1:
                    shutil.rmtree(run_dir, ignore_errors=True)
                    return self._send_json({"error": "upload a single capture-session .zip"}, status=400)
                item = session_uploads[0]
                if Path(str(item.filename)).suffix.lower() != ".zip":
                    shutil.rmtree(run_dir, ignore_errors=True)
                    return self._send_json({"error": "capture session must be a .zip"}, status=400)
                zip_path = run_dir / "incoming-session.zip"
                with zip_path.open("wb") as out:
                    shutil.copyfileobj(item.file, out, length=1024 * 1024)
                try:
                    session = import_session(zip_path, run_dir)
                except CaptureSessionError as exc:
                    shutil.rmtree(run_dir, ignore_errors=True)
                    return self._send_json({"error": str(exc)}, status=400)
                zip_path.unlink(missing_ok=True)
                saved = [
                    path.relative_to(source_dir).as_posix()
                    for path in sorted(source_dir.rglob("*"))
                    if path.is_file()
                ]
                if not title or title == "New capture":
                    title = session.title
                if not subject or subject == "Not recorded.":
                    subject = session.subject
                session_info = {
                    "session_id": session.session_id,
                    "warnings": session.warnings,
                    "stills": len(session.stills),
                    "videos": len(session.videos),
                }
            else:
                for item in uploads:
                    original = Path(str(item.filename)).name
                    ext = Path(original).suffix.lower()
                    if ext not in UPLOAD_SUFFIXES:
                        continue
                    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip(".-") or "media"
                    destination = source_dir / f"{stem[:100]}{ext}"
                    counter = 2
                    while destination.exists():
                        destination = source_dir / f"{stem[:92]}-{counter}{ext}"
                        counter += 1
                    with destination.open("wb") as out:
                        shutil.copyfileobj(item.file, out, length=1024 * 1024)
                    saved.append(destination.name)

            if not saved:
                shutil.rmtree(run_dir, ignore_errors=True)
                return self._send_json({"error": "no supported image or video files were selected"}, status=400)

            command = [
                sys.executable, "-m", "vitrine",
                "--run-dir", str(run_dir),
                "--quality", quality,
                "run",
                "--source", str(source_dir),
                "--originals", str(source_dir),
                "--title", title,
                "--subject", subject,
                "--capture-type", capture_type,
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ticket = {
                "title": title, "subject": subject, "quality": quality, "capture_type": capture_type, "files": saved,
            }
            if session_info:
                ticket["source"] = "capture-session"
                ticket["session_id"] = session_info["session_id"]
                ticket["warnings"] = session_info["warnings"]
            (run_dir / "capture.json").write_text(json.dumps(ticket, indent=2), encoding="utf-8")
            with (run_dir / "capture.log").open("ab") as capture_log:
                process = subprocess.Popen(
                    command,
                    cwd=self.project_root,
                    stdin=subprocess.DEVNULL,
                    stdout=capture_log,
                    stderr=subprocess.STDOUT,
                    creationflags=creation_flags,
                )
            self.capture_processes[name] = process
            if self.run_allowlist is not None:
                self.run_allowlist.add(name)
            payload = {
                "ok": True,
                "name": name,
                "title": title,
                "quality": quality,
                "files": saved,
                "process_id": process.pid,
                "capture_type": capture_type,
            }
            if session_info:
                payload["session"] = session_info
            return self._send_json(payload, status=202)
        except (OSError, ValueError) as exc:
            logger.exception("capture upload failed")
            return self._send_json({"error": str(exc)}, status=500)
        finally:
            if form is not None:
                form.close()


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    on_ready=None,
    project_root: Path | None = None,
    only: list[str] | set[str] | None = None,
) -> None:
    """Start the dashboard and block until interrupted.

    *only* — optional run directory names to show. Other folders under
    ``runs/`` remain on disk but are hidden from the library and detail APIs.
    """
    root = (project_root or PROJECT_ROOT).resolve()
    runs_root = root / "runs"
    ui_dir = Path(__file__).resolve().parent / "ui"
    if not (ui_dir / "index.html").is_file():
        raise FileNotFoundError(f"UI assets missing under {ui_dir}")

    allowlist: set[str] | None = None
    if only:
        allowlist = {n.strip() for n in only if str(n).strip()}
        if not allowlist:
            allowlist = None

    handler = partial(VitrineHandler)
    # Class attrs shared by all request threads.
    VitrineHandler.project_root = root
    VitrineHandler.runs_root = runs_root
    VitrineHandler.ui_dir = ui_dir
    VitrineHandler.run_allowlist = allowlist

    # Bind with reuse so a quick restart after Ctrl-C works.
    class ReusableServer(ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    # If the preferred port is taken, walk up a few numbers.
    server: ThreadingHTTPServer | None = None
    bound_port = port
    last_err: OSError | None = None
    for candidate in range(port, port + 10):
        try:
            server = ReusableServer((host, candidate), handler)
            bound_port = candidate
            break
        except OSError as exc:
            last_err = exc
            continue
    if server is None:
        raise RuntimeError(f"could not bind {host}:{port}: {last_err}") from last_err

    url = f"http://{host}:{bound_port}/"
    logger.info("Vitrine UI  %s  (runs: %s)", url, runs_root)
    print(f"\n  Vitrine UI  →  {url}")
    print(f"  runs root   →  {runs_root}")
    if allowlist:
        print(f"  showing only →  {', '.join(sorted(allowlist))}")
    print("  Ctrl-C to stop.\n")

    if on_ready is not None:
        on_ready(url, server)

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        server.server_close()


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True
