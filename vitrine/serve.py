"""Local web UI for creating captures and inspecting runs on disk.

Surfaces what the pipeline has produced — stage reports, metrics, artefacts,
and the interactive splat viewer. New image/video uploads start the existing
CLI pipeline in a background process rather than reimplementing its stages.

Start with::

    python -m vitrine ui
    python -m vitrine ui --port 8765 --open
"""

from __future__ import annotations

import cgi
import json
import logging
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, profiles

logger = logging.getLogger(__name__)

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


def _run_slug(value: str) -> str:
    """Return a filesystem-safe run name derived from a human title."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:64] or "new-capture"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


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

    order = ("ingest", "sfm", "train", "evaluate", "package", "view")
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
    from urllib.parse import quote

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
            thumb_rel = f"objects/{oid}/turntable/view_00.png"
            candidate = (run_dir / thumb_rel).resolve()
            if candidate.is_relative_to(run_dir.resolve()) and candidate.is_file():
                thumb_url = f"/files/{quote(name)}/{quote(thumb_rel)}"
        items.append({
            "object_id": oid if safe_component(oid) else None,
            "label": rec.get("label") if isinstance(rec.get("label"), str) else None,
            "confidence": rec.get("confidence"),
            "coverage": rec.get("coverage"),
            "thumb_url": thumb_url,
        })
    return {"count": len(items), "objects": items}


def _summarise_run(run_dir: Path) -> dict[str, Any]:
    name = run_dir.name
    status = _stage_status(run_dir)
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
        "train_log": _file_info(run_dir / "logs" / "vitrine.log") or _file_info(run_dir / "logs" / "train-standard.log"),
    }

    running = status["stages"]["train"]["running"]
    interrupted = status["stages"]["train"]["interrupted"]
    # progress.json (running=True, or a stale interrupted mid-run) has no
    # final_psnr/minutes/peak_vram_gb — those only exist on train.json. Fall
    # back to the most recent periodic eval in its history so headline cards
    # aren't blank for live *or* interrupted runs.
    last_eval = (train.get("history") or [{}])[-1] if (running or interrupted) else {}

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
        "path": str(run_dir.relative_to(PROJECT_ROOT)) if run_dir.is_relative_to(PROJECT_ROOT) else str(run_dir),
        "updated_mtime": updated_mtime,
        "updated_at": updated_at,
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
            "psnr": train.get("final_psnr", last_eval.get("psnr")),
            "ssim": train.get("final_ssim", last_eval.get("ssim")),
            "n_gaussians": train.get("n_gaussians"),
            "minutes": train.get("minutes", train.get("elapsed_minutes")),
            "peak_vram_gb": train.get("peak_vram_gb"),
            "iterations": train.get("iterations"),
            "running": running,
            "interrupted": interrupted,
            "step": train.get("step") if (running or interrupted) else None,
            "eta_minutes": train.get("eta_minutes") if running else None,
            "energy_kwh": energy_kwh,
            "cost_gbp": current_cost_gbp,
            "electricity_rate_gbp_per_kwh": electricity_rate,
        },
        "artefacts": artefacts,
        "objects": _objects_summary(run_dir),
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
        if not path.is_dir():
            continue
        if only is not None and path.name not in only:
            continue
        # Skip non-run directories (logs dropped at root of runs/, etc.)
        markers = (
            path / "ingest",
            path / "sfm",
            path / "model",
            path / "archive",
        )
        if not any(m.exists() for m in markers):
            continue
        runs.append(_summarise_run(path))
    # Most recently touched first (by train.json or dir mtime).
    def sort_key(r: dict[str, Any]) -> float:
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
    docker_gpu: bool | None = None
    if docker:
        try:
            from .sfm import gpu_available

            docker_gpu = bool(gpu_available())
        except Exception:  # noqa: BLE001
            docker_gpu = False

    ok = bool(
        status.get("cuda_root")
        and status.get("host_compiler")
        and gpu.get("available")
        and docker
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
        },
        "profile_preview": profiles.describe(profiles.resolve("standard", tier)),
    }


def _profiles_payload() -> list[dict[str, Any]]:
    rows = []
    for tier in profiles.TIERS:
        for quality in profiles.QUALITY_LEVELS:
            p = profiles.resolve(quality, tier)
            rows.append({
                **profiles.describe(p),
                "estimated_minutes": round(p.estimated_minutes(), 1),
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
        except BrokenPipeError:
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

        if path == "/api/runs":
            return self._send_json({
                "runs": _list_runs(self.runs_root, only=self.run_allowlist),
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
                detail = _summarise_run(run_dir)
                detail["samples"] = _list_image_samples(run_dir)
                # Include full reports for the detail pane (already in stages).
                return self._send_json(detail)
            if len(parts) == 2 and parts[1] == "log":
                qs = parse_qs(parsed.query)
                which = (qs.get("which") or ["train"])[0]
                candidates = {
                    "train": [
                        run_dir / "logs" / "vitrine.log",
                        run_dir / "logs" / "train-standard.log",
                        run_dir / "logs" / "train.log",
                    ],
                    "sfm": [
                        run_dir / "logs" / "vitrine.log",
                        run_dir / "logs" / "sfm.log",
                        run_dir / "sfm" / "colmap.log",
                    ],
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
            if not (run_dir / "model" / "scene.splat").is_file():
                return self._send_text("no scene.splat for this run", status=404)
            return self._send_file(self.ui_dir / "viewer.html")

        return self._send_text("not found", status=404)

    def do_POST(self) -> None:  # noqa: N802
        """Accept local capture media and start the existing CLI pipeline."""
        parsed = urlparse(self.path)
        if parsed.path != "/api/captures":
            return self._send_json({"error": "unknown endpoint"}, status=404)

        content_type = self.headers.get("Content-Type", "")
        if not content_type.startswith("multipart/form-data"):
            return self._send_json({"error": "multipart form data required"}, status=400)

        try:
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": content_type,
                    "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
                },
                keep_blank_values=True,
            )
            title = str(form.getfirst("title", "New capture")).strip() or "New capture"
            subject = str(form.getfirst("subject", "Not recorded.")).strip() or "Not recorded."
            quality = str(form.getfirst("quality", "standard")).strip().lower()
            if quality not in profiles.QUALITY_LEVELS:
                return self._send_json({"error": "invalid quality level"}, status=400)

            uploads = form["files"] if "files" in form else []
            if not isinstance(uploads, list):
                uploads = [uploads]
            uploads = [item for item in uploads if getattr(item, "filename", None)]
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
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                creationflags=creation_flags,
            )
            if self.run_allowlist is not None:
                self.run_allowlist.add(name)
            return self._send_json({
                "ok": True,
                "name": name,
                "title": title,
                "quality": quality,
                "files": saved,
                "process_id": process.pid,
            }, status=202)
        except (OSError, ValueError) as exc:
            logger.exception("capture upload failed")
            return self._send_json({"error": str(exc)}, status=500)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
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
