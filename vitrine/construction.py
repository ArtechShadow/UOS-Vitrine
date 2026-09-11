"""Best-effort observation of reconstruction; never an input to optimisation.

Only files owned by a SnapshotStore may be pruned. Masters, source media and
training checkpoints are deliberately outside this namespace.
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

STATUS_SCHEMA = "vitrine/construction-status/1"
DEFAULT_HEARTBEAT_SECONDS = 2.0
DEFAULT_STALE_SECONDS = 30.0

_TRAINING_SAVE_SCHEMA = "vitrine/training-save/1"
_VERIFIED_MASTER_STATES = frozenset({
    "master_saved",
    "master_saved_evaluation_failed",
    "master_saved_with_evaluation_warning",
    "complete",
})
_STAGE_OUTPUTS = {
    "ingest": ("ingest/ingest.json",),
    "sfm": ("sfm/sfm.json", "sfm/sparse_text/cameras.txt",
            "sfm/sparse_text/images.txt", "sfm/sparse_text/points3D.txt"),
    "train": ("model/train.json", "model/scene.ply"),
    "evaluate": ("model/evaluation.json",),
    "cleanup": ("model/scene.cleaned.ply",),
    "export": ("model/scene.splat",),
    "package": ("archive/manifest.json",),
}


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _sha256_file(path: Path, size: int, mtime_ns: int, inode: int) -> str:
    """Hash a status artefact once per observed file generation.

    ``construction_payload`` is polled by the dashboard.  Caching by the
    immutable file metadata avoids re-reading a several-hundred-megabyte
    master on every poll while still invalidating an atomic replacement or a
    normal in-place edit.
    """
    del size, mtime_ns, inode  # metadata is part of the cache key
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# A bounded manual cache keeps this module compatible with older Python builds
# while avoiding unbounded entries for repeated experimental runs.
_FILE_DIGEST_CACHE: dict[tuple[str, int, int, int], str] = {}


def _file_digest(path: Path) -> str | None:
    try:
        stat = Path(path).stat()
        if not Path(path).is_file() or Path(path).is_symlink():
            return None
        key = (str(Path(path).resolve()), int(stat.st_size),
               int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9))),
               int(getattr(stat, "st_ino", 0)))
        cached = _FILE_DIGEST_CACHE.get(key)
        if cached is not None:
            return cached
        value = _sha256_file(Path(path), key[1], key[2], key[3])
        if len(_FILE_DIGEST_CACHE) >= 128:
            _FILE_DIGEST_CACHE.pop(next(iter(_FILE_DIGEST_CACHE)))
        _FILE_DIGEST_CACHE[key] = value
        return value
    except OSError:
        return None


def _verified_master_artifact(training: Path, save_state: dict | None) -> dict | None:
    """Return the current master receipt only after its marker and digest agree."""
    if not isinstance(save_state, dict):
        return None
    if (save_state.get("schema") != _TRAINING_SAVE_SCHEMA
            or save_state.get("state") not in _VERIFIED_MASTER_STATES
            or save_state.get("verified") is not True):
        return None
    artifact = save_state.get("artifact")
    if not isinstance(artifact, dict) or artifact.get("path") != "scene.ply":
        return None
    expected = artifact.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return None
    path = Path(training) / "scene.ply"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if artifact.get("bytes") != path.stat().st_size:
            return None
    except OSError:
        return None
    actual = _file_digest(path)
    if actual is None or actual != expected.lower():
        return None
    return dict(artifact, sha256=actual)


def _stage_outputs_match(run_dir: Path, name: str, record: dict, marker_done: bool) -> bool:
    """Require a complete pipeline marker, output fingerprint and files."""
    if not marker_done or record.get("state") != "complete":
        return False
    outputs = record.get("outputs")
    required = _STAGE_OUTPUTS.get(name)
    if not isinstance(outputs, dict) or not required:
        return False
    for relative in required:
        expected = outputs.get(relative)
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            return False
        path = Path(run_dir) / relative
        if _file_digest(path) != expected.lower():
            return False
    return True


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        encoded = json.dumps(value, allow_nan=False)
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows readers can briefly hold the destination without delete-share,
        # making an otherwise atomic replace fail with WinError 5. Dashboard
        # polling must not be able to abort a reconstruction at a stage boundary.
        for attempt in range(6):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
        if os.name != "nt":
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                logger.debug("Could not fsync JSON parent directory", exc_info=True)
    finally:
        tmp.unlink(missing_ok=True)


def process_alive(pid):
    """Return whether a process id is currently alive when the OS can tell us."""
    from .processes import process_alive as check_alive
    return check_alive(pid)


def recover_stale_progress(folder, *, stale_after=DEFAULT_STALE_SECONDS, now=None):
    """Mark a vanished or silent worker ``unknown`` while retaining its record.

    A process can disappear before its ``finally`` block writes a terminal
    status (for example during a native CUDA call).  Leaving ``running`` in the
    status file makes a failed run look active forever and can make a browser
    report false progress.  This helper writes an explicit diagnostic and is
    idempotent; a live worker is never changed.
    """
    path = Path(folder)
    if path.is_dir():
        status_path = path / "construction-status.json"
    elif path.name == "construction-status.json":
        status_path = path
    else:
        status_path = path
    status = read_json(status_path)
    if not status or status.get("state") != "running":
        return status
    now = time.time() if now is None else float(now)
    heartbeat = status.get("heartbeat", status.get("started", 0))
    try:
        age = max(0.0, now - float(heartbeat))
    except (TypeError, ValueError):
        age = float("inf")
    alive = process_alive(status.get("pid"))
    vanished = alive is False
    silent = age > float(stale_after)
    if not vanished and not silent:
        return status
    reason = "worker process is no longer alive" if vanished else (
        f"no heartbeat for {age:.1f}s (threshold {float(stale_after):.1f}s)"
    )
    updated = dict(status)
    updated.update(
        state="unknown",
        failure_kind="worker_disappeared" if vanished else "heartbeat_timeout",
        error=f"Construction worker status is unknown: {reason}",
        recovered_at=now,
        heartbeat=now,
    )
    try:
        atomic_json(status_path, updated)
    except (OSError, ValueError):
        logger.debug("Could not persist stale construction status", exc_info=True)
    return updated


def snapshot_records(path):
    records = (read_json(path) or {}).get("snapshots", [])
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)
            and re.fullmatch(r"preview-[0-9a-f]{32}\.(json|splat)", str(r.get("id", "")))
            and r.get("kind") in ("sparse", "splat")
            and isinstance(r.get("created"), (int, float))]


class Progress:
    """Durable worker lifecycle with heartbeats during long native calls."""

    def __init__(self, folder, stage, *, heartbeat_seconds=None):
        self.path = Path(folder) / "construction-status.json"
        interval = heartbeat_seconds
        if interval is None:
            try:
                interval = float(os.environ.get("VITRINE_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS))
            except ValueError:
                interval = DEFAULT_HEARTBEAT_SECONDS
        self.heartbeat_seconds = max(0.1, float(interval))
        previous = recover_stale_progress(self.path)
        started = time.time()
        self.data = {
            "schema": STATUS_SCHEMA,
            "stage": stage,
            "state": "running",
            "started": started,
            "heartbeat": started,
            "pid": os.getpid(),
        }
        if previous and previous.get("state") == "unknown":
            self.data["recovered_from"] = {
                "state": previous.get("state"),
                "failure_kind": previous.get("failure_kind"),
                "pid": previous.get("pid"),
                "recovered_at": previous.get("recovered_at"),
            }
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)

    def update(self, **values):
        with self.lock:
            self.data.update(values, heartbeat=time.time())
            try:
                atomic_json(self.path, self.data)
            except (OSError, ValueError):
                logger.debug("Could not publish construction status", exc_info=True)

    def _heartbeat(self):
        while not self.stop.wait(self.heartbeat_seconds):
            self.update()

    def __enter__(self):
        self.update()
        self.thread.start()
        return self

    def __exit__(self, typ, exc, tb):
        self.stop.set()
        self.thread.join()
        final_state = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed" if exc else "complete"
        self.update(
            state=final_state,
            error=str(exc) if exc else None,
            seconds=time.time() - self.data["started"],
            finished=time.time(),
        )


class SnapshotStore:
    def __init__(self, folder):
        owner = Path(folder)
        if owner.name in ("model", "sfm") and owner.parent.name != "experiments":
            owner = owner.parent
        self.folder = owner / "construction" / "live"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.manifest = self.folder / "manifest.json"
        self.records = snapshot_records(self.manifest)

    def publish(self, kind, suffix, writer, **metadata):
        name = "preview-" + uuid.uuid4().hex + suffix
        target = self.folder / name
        temporary = self.folder / (name + ".tmp")
        try:
            writer(temporary)
            if temporary.stat().st_size == 0:
                raise ValueError("No visible geometry in this preview yet")
            temporary.replace(target)
            record = {"id": name, "kind": kind, "created": time.time(), **metadata}
            records = self.records + [record]
            # Uniformly thin the oldest half, keeping recent playback smooth.
            removed = []
            if len(records) > 120:
                removed = records[1:60:2]
                records = [r for r in records if r not in removed]
            atomic_json(self.manifest, {"snapshots": records})
            self.records = records
            for old in removed:
                owned = self.folder / old["id"]
                if owned.parent == self.folder and owned.name.startswith("preview-"):
                    owned.unlink(missing_ok=True)
            return record
        finally:
            temporary.unlink(missing_ok=True)


def sparse_payload(model):
    import numpy as np
    n = len(model.points_xyz)
    indices = np.linspace(0, n - 1, min(n, 100_000), dtype=int) if n else []
    points = model.points_xyz[indices]
    if not np.isfinite(points).all():
        raise ValueError("Non-finite sparse geometry")
    cameras = []
    for view in model.images:
        camera = model.camera_for(view)
        cameras.append({"id": view.id, "name": view.name, "camera_id": camera.id,
                        "width": camera.width, "height": camera.height,
                        "intrinsics": camera.intrinsic_matrix().tolist(),
                        "camera_to_world": np.linalg.inv(view.world_to_camera()).tolist()})
    return {"positions": points.reshape(-1).tolist(),
            "colors": model.points_rgb[indices].reshape(-1).tolist(), "cameras": cameras}


class TrainingPreview:
    """One CPU export in flight. GPU copies happen only at safe loop boundaries."""

    def __init__(self, folder, scene_scale, max_scale_fraction):
        self.enabled = os.environ.get("VITRINE_LIVE_PREVIEWS", "1") != "0"
        self.folder = Path(folder)
        self.scale = scene_scale * max_scale_fraction
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="splat-preview")
        self.future = None
        self.last = -float("inf")
        self.store = None

    def capture(self, params, step, force=False):
        if not self.enabled:
            return
        try:
            if self.future and not self.future.done():
                if not force:
                    return
                self.future.result()
            if not force and time.monotonic() - self.last < 5:
                return
            import torch
            count = len(params["means"])
            with torch.no_grad():
                # Integer arithmetic, no random sampling and no mutation of parameters.
                indices = torch.arange(min(count, 250_000), device=params["means"].device)
                indices = indices * count // max(1, len(indices))
                data = {k: params[k].detach().index_select(0, indices).cpu().numpy().copy()
                        for k in ("means", "scales", "quats", "opacities", "sh0")}
            captured = time.time()
            self.last = time.monotonic()
            self.future = self.pool.submit(self._export, data, step, count, captured)
        except Exception as exc:
            self._error(exc)

    def _error(self, exc):
        logger.warning("Live preview unavailable: %s", exc)
        try:
            atomic_json(self.folder / "construction-preview-error.json",
                        {"error": str(exc), "updated": time.time()})
        except OSError:
            pass

    def _export(self, data, step, count, captured):
        from .ply import write_splat_ply
        from .export import write_splat_file
        import numpy as np
        try:
            if self.store is None:
                self.store = SnapshotStore(self.folder)
            scratch = self.store.folder / "sample.ply"
            try:
                write_splat_ply(scratch, **data,
                                shN=np.empty((len(data["means"]), 0, 3)),
                                sh_degree=0, max_scale=self.scale)
                self.store.publish("splat", ".splat", lambda p: write_splat_file(scratch, p),
                                   step=step, total_gaussians=count,
                                   sampled_gaussians=len(data["means"]), captured=captured,
                                   reduced_detail=True)
                (self.folder / "construction-preview-error.json").unlink(missing_ok=True)
            finally:
                scratch.unlink(missing_ok=True)
        except Exception as exc:
            self._error(exc)

    def close(self):
        self.pool.shutdown(wait=True)


def construction_payload(run_dir, process=None, folder=None):
    """Additive API for new and historical runs, including external experiments."""
    run_dir = Path(run_dir)
    training = folder or run_dir / "model"
    if folder is not None and (training / "model").is_dir() and not (training / "model").is_symlink():
        training = training / "model"
    folders = [run_dir, run_dir / "ingest", run_dir / "sfm", training] if folder is None else [training]
    statuses = [s for p in folders if (s := recover_stale_progress(p))]
    latest = max(statuses, key=lambda s: s.get("heartbeat", 0), default={})
    pipeline = recover_stale_progress(run_dir) if folder is None else None
    if pipeline and pipeline.get("state") == "running":
        child = {"ingest": run_dir / "ingest", "sfm": run_dir / "sfm", "train": training, "evaluate": training}.get(pipeline.get("stage"))
        candidate = recover_stale_progress(child) if child else None
        latest = candidate if candidate and candidate.get("started", 0) >= pipeline.get("started", 0) else pipeline
    payload = dict(latest)
    if folder is None:
        from .sfm_visual import feature_preview, matching_preview, mapper_preview
        payload["feature_preview"] = feature_preview(run_dir)
        if latest.get('stage') == 'sfm':
            payload['matching_preview'] = matching_preview(run_dir)
            if latest.get('substage') == 'mapper':
                payload['mapper_preview'] = mapper_preview(run_dir)
    selection = read_json(run_dir / "ingest/selection.json") if folder is None else None
    if selection:
        for record in selection.get("records", []):
            thumb = record.pop("thumbnail", None)
            if thumb and re.fullmatch(r"[0-9a-f]{32}\.jpg", str(thumb)):
                record["url"] = "/files/" + quote(run_dir.name, safe="") + "/ingest/selection-thumbnails/" + thumb
        payload["selection"] = selection
    progress = read_json(training / "progress.json") or {}
    complete = read_json(training / "train.json")
    save_state = read_json(training / "save-state.json")
    master_artifact = _verified_master_artifact(training, save_state)
    snapshots, images = [], []
    roots = [run_dir, run_dir / "sfm", training] if folder is None else [training]
    for root in roots:
        for entry in snapshot_records(root / "construction/live/manifest.json"):
            path = root / "construction/live" / entry["id"]
            if path.is_file() and not path.is_symlink() and path.parent == root / "construction/live":
                snapshots.append({**entry, "url": "/files/" + quote(run_dir.name, safe="") + "/" +
                                  quote(path.relative_to(run_dir).as_posix(), safe="/")})
        for path in sorted((root / "construction").glob("step-*.json")):
            meta = read_json(path)
            if meta and path.with_suffix(".jpg").is_file():
                images.append({**meta, "url": "/files/" + quote(run_dir.name, safe="") + "/" +
                               quote(path.with_suffix(".jpg").relative_to(run_dir).as_posix(), safe="/")})
    snapshots.sort(key=lambda s: s["created"])
    # Older experiments recorded evaluation images only. Keep their real replay
    # history without manufacturing intermediate geometry or iterations.
    if not snapshots:
        snapshots = [{**entry, "id": entry["url"], "kind": "render",
                      "created": entry.get("recorded_at", 0)} for entry in images]
    payload.update(snapshots=snapshots, images=images, training=complete or progress,
                   save_state=save_state,
                   preview_error=read_json(training / "construction-preview-error.json") or payload.get("preview_error"))
    payload["master_saved"] = master_artifact is not None
    payload["master_artifact"] = master_artifact
    evaluation = read_json(training / "evaluation.json")
    payload["evaluation"] = evaluation
    payload['postprocessing'] = [dict(folder=name, **status) for name in ('object-meshes','scene-mesh','pbr')
                                 if (status := read_json(run_dir/name/'construction-status.json'))]
    payload['surface_assets'] = []
    pbr_manifest = read_json(run_dir/'pbr/manifest.json') or {}
    for record in pbr_manifest.get('objects', []):
        oid = record.get('object_id', '')
        if not isinstance(oid, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', oid):
            continue
        rel = f'pbr/{oid}/model.glb'
        if (run_dir/rel).is_file():
            payload['surface_assets'].append(dict(label=record.get('label',oid),path=rel))
    scene_manifest = read_json(run_dir/'scene-mesh/manifest.json') or {}
    for name in scene_manifest.get('outputs', {}):
        if re.fullmatch(r'[A-Za-z0-9_.-]+\.glb', name) and (run_dir/'scene-mesh'/name).is_file():
            payload['surface_assets'].append(dict(label='XR Lab scene surface',path='scene-mesh/'+name))
    payload["evaluation_progress"] = read_json(training / "evaluation-progress.json")
    payload["packaging"] = read_json(run_dir / "construction-package.json")
    manifest = read_json(run_dir / "archive/manifest.json")
    if manifest:
        payload["packaging"] = dict(complete=True, copied=manifest.get("file_count"),
                                    checksummed=manifest.get("file_count"), bytes=manifest.get("total_bytes"))
        payload["manifest_url"] = "/files/" + quote(run_dir.name, safe="") + "/archive/manifest.json"
    done = {"ingest": (run_dir / "ingest/ingest.json").is_file(),
            "sfm": (run_dir / "sfm/sfm.json").is_file(),
            "train": bool(complete and master_artifact),
            "evaluate": bool(master_artifact and (evaluation is not None
                                                   or (complete and complete.get("final_psnr") is not None))),
            "package": (run_dir / "archive/manifest.json").is_file()}
    payload["done"] = done
    payload.setdefault("stage", "train" if progress or complete else "ingest")
    payload.setdefault("state", "complete" if complete else "unknown")
    if process is not None:
        code = process.poll()
        payload["state"] = "running" if code is None else "complete" if code == 0 else "failed"
        payload["returncode"] = code
    elif payload["state"] == "running" and time.time() - payload.get("heartbeat", 0) > 30:
        payload["state"] = "unknown"
    if payload["state"] == "running":
        order = list(done)
        if payload["stage"] in order:
            for stage in order[order.index(payload["stage"]):]:
                done[stage] = False
        if payload["stage"] in ("train", "evaluate"):
            payload["training"] = progress
    if not latest and progress and not complete:
        payload["state"] = "unknown"
    source = training / "construction/source.jpg"
    payload["source_url"] = "/files/" + quote(run_dir.name, safe="") + "/" + quote(source.relative_to(run_dir).as_posix(), safe="/") if source.is_file() else None
    payload["historical"] = not snapshots and bool(complete) and payload["state"] != "running"
    payload["final_url"] = ("/viewer/" + quote(run_dir.name, safe="")) if folder is None and (training / "scene.splat").is_file() else None
    if folder is None:
        state = read_json(run_dir / "pipeline.json")
        payload["pipeline"] = state
        payload["preflight"] = read_json(run_dir / "preflight.json")
        payload["timings"] = read_json(run_dir / "timing-summary.json")
        if state:
            for stage, record in state.get("stages", {}).items():
                done[stage] = (_stage_outputs_match(run_dir, stage, record, done.get(stage, False))
                               if isinstance(record, dict) else False)
            if state.get("state") in ("failed", "cancelled", "complete"):
                payload["state"] = state["state"]
                payload["error"] = state.get("error")
            # A terminal marker without matching output receipts is an
            # incomplete/unknown run.  Keep the evidence visible and make the
            # dashboard eligible to retry rather than reporting a false pass.
            if state.get("state") == "complete":
                expected_stages = [name for name in state.get("stages", {})
                                   if name in _STAGE_OUTPUTS]
                invalid = [name for name in expected_stages if not done.get(name, False)]
                if invalid:
                    payload["state"] = "unknown"
                    payload["error"] = (
                        "Pipeline reported completion but required stage marker/output "
                        "fingerprint is missing or changed: " + ", ".join(invalid)
                    )
            payload["can_resume"] = state.get("state") in ("failed", "cancelled") or payload["state"] == "unknown"
        payload["cancel_requested"] = (run_dir / "cancel.request").is_file()
    return payload
