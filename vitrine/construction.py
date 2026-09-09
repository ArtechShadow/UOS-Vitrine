"""Best-effort observation of reconstruction; never an input to optimisation.

Only files owned by a SnapshotStore may be pruned. Masters, source media and
training checkpoints are deliberately outside this namespace.
"""
from __future__ import annotations

import json
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


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        tmp.write_text(json.dumps(value, allow_nan=False), encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def snapshot_records(path):
    records = (read_json(path) or {}).get("snapshots", [])
    if not isinstance(records, list):
        return []
    return [r for r in records if isinstance(r, dict)
            and re.fullmatch(r"preview-[0-9a-f]{32}\.(json|splat)", str(r.get("id", "")))
            and r.get("kind") in ("sparse", "splat")
            and isinstance(r.get("created"), (int, float))]


class Progress:
    """Heartbeat continues during long native calls; errors remain observable."""

    def __init__(self, folder, stage):
        self.path = Path(folder) / "construction-status.json"
        self.data = {"stage": stage, "state": "running", "started": time.time()}
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
        while not self.stop.wait(2):
            self.update()

    def __enter__(self):
        self.update()
        self.thread.start()
        return self

    def __exit__(self, typ, exc, tb):
        self.stop.set()
        self.thread.join()
        self.update(state="failed" if exc else "complete", error=str(exc) if exc else None)


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
    folders = [run_dir, run_dir / "ingest", run_dir / "sfm", training] if folder is None else [training]
    statuses = [s for p in folders if (s := read_json(p / "construction-status.json"))]
    latest = max(statuses, key=lambda s: s.get("heartbeat", 0), default={})
    pipeline = read_json(run_dir / "construction-status.json") if folder is None else None
    if pipeline and pipeline.get("state") == "running":
        child = {"ingest": run_dir / "ingest", "sfm": run_dir / "sfm", "train": training}.get(pipeline.get("stage"))
        candidate = read_json(child / "construction-status.json") if child else None
        latest = candidate if candidate and candidate.get("started", 0) >= pipeline.get("started", 0) else pipeline
    payload = dict(latest)
    progress = read_json(training / "progress.json") or {}
    complete = read_json(training / "train.json")
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
                   preview_error=read_json(training / "construction-preview-error.json") or payload.get("preview_error"))
    done = {"ingest": (run_dir / "ingest/ingest.json").is_file(),
            "sfm": (run_dir / "sfm/sfm.json").is_file(), "train": bool(complete),
            "evaluate": (training / "evaluation.json").is_file() or bool(complete and complete.get("final_psnr") is not None),
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
    return payload
