"""Durable stage boundaries, exclusive run ownership and cooperative cancellation."""
from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from .construction import (
    DEFAULT_STALE_SECONDS,
    Progress,
    atomic_json,
    process_alive,
    read_json,
)
from .telemetry import measure, timing_summary

PIPELINE_COMPATIBILITY = {
    "source_identity": "sha256-content-v2",
    "training_save": "vitrine/training-save/1",
    "ply_format": "vitrine/gaussian-ply/binary-little-endian-1.0",
}


def current_compatibility():
    """Return the resume contract plus a digest of code that affects stages."""
    digest = hashlib.sha256()
    package_root = Path(__file__).resolve().parent
    for name in ("pipeline.py", "train.py", "ply.py", "construction.py", "telemetry.py",
                 "ingest.py", "dataset.py", "colmap_io.py", "undistort.py", "frame_selection.py",
                 "sfm.py", "profiles.py", "cli.py", "engines.py", "export.py", "package.py"):
        path = package_root / name
        digest.update(name.encode("utf-8"))
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            digest.update(b"<missing>")
    return {**PIPELINE_COMPATIBILITY, "implementation_sha256": digest.hexdigest()}


def check_cancel(run_dir):
    if (Path(run_dir) / "cancel.request").exists():
        raise KeyboardInterrupt("Cancellation requested; completed stages are retained")


@contextmanager
def run_lock(run_dir):
    path = Path(run_dir) / "pipeline.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if not handle.read(1):
                    handle.write(b"0"); handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("This capture already has a running pipeline") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            value.update(chunk)
    return value.hexdigest()


def source_identity(source):
    source = Path(source)
    from .ingest import IMAGE_SUFFIXES, VIDEO_SUFFIXES
    paths = [source] if source.is_file() else sorted(source.rglob("*"))
    records = []
    for path in paths:
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES | VIDEO_SUFFIXES:
            continue
        records.append({
            "name": str(path.relative_to(source)) if source.is_dir() else path.name,
            "bytes": path.stat().st_size,
            "sha256": digest_file(path),
        })
    return hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


OUTPUTS = {
    "ingest": ["ingest/ingest.json"],
    "sfm": ["sfm/sfm.json", "sfm/sparse_text/cameras.txt", "sfm/sparse_text/images.txt", "sfm/sparse_text/points3D.txt"],
    "train": ["model/train.json", "model/scene.ply"],
    "evaluate": ["model/evaluation.json"],
    "cleanup": ["model/scene.cleaned.ply"],
    "export": ["model/scene.splat"],
    "package": ["archive/manifest.json"],
}


def _recover_running_stages(state, run_dir=None, *, now=None):
    """Turn a stage left ``running`` by a vanished worker into ``unknown``.

    Stage outputs are deliberately retained for inspection.  The next resume
    will rerun the unknown stage unless its state is explicitly verified as
    complete, so a stale JSON record can never make a partial training run look
    successful.
    """
    now = time.time() if now is None else float(now)
    recovered = []
    pipeline_status = read_json(Path(run_dir) / "construction-status.json") if run_dir else None
    for name, record in state.get("stages", {}).items():
        if not isinstance(record, dict) or record.get("state") != "running":
            continue
        live_status = pipeline_status if pipeline_status and pipeline_status.get("stage") == name else None
        heartbeat = (live_status or {}).get("heartbeat", record.get("heartbeat", record.get("started", 0)))
        try:
            age = max(0.0, now - float(heartbeat))
        except (TypeError, ValueError):
            age = float("inf")
        alive = process_alive((live_status or {}).get("pid", record.get("pid")))
        if alive is not False and age <= DEFAULT_STALE_SECONDS:
            continue
        reason = "worker process is no longer alive" if alive is False else (
            f"no heartbeat for {age:.1f}s (threshold {DEFAULT_STALE_SECONDS:.1f}s)"
        )
        record.update(
            state="unknown",
            failure_kind="worker_disappeared" if alive is False else "heartbeat_timeout",
            error=f"Pipeline stage status is unknown: {reason}",
            recovered_at=now,
            heartbeat=now,
        )
        recovered.append({"stage": name, "reason": reason, "at": now})
    if recovered:
        state.setdefault("recovery", []).extend(recovered)
    return recovered


def _invalidate_incompatible_stages(state):
    """Return a reason when a saved pipeline cannot safely be resumed.

    Compatibility changes are intentionally a hard boundary.  Re-running the
    first stage into an existing run would move accepted ingest/SfM outputs and
    make their provenance ambiguous.  The caller records the refusal and asks
    for a new run directory, leaving every old artifact untouched.
    """
    saved = state.get("compatibility")
    expected = current_compatibility()
    if saved == expected:
        return None
    reason = "pipeline compatibility record is missing" if saved is None else (
        f"pipeline compatibility changed ({saved!r} -> {expected!r})"
    )
    return reason


def fingerprint(run_dir, stage):
    root = Path(run_dir)
    files = OUTPUTS[stage]
    result = {}
    for name in files:
        path = root / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"{stage} is missing a complete output: {name}")
        result[name] = digest_file(path)
    if stage == "ingest":
        images = sorted((root / "ingest/images").rglob("*"))
        records = [(p.relative_to(root).as_posix(), p.stat().st_size, digest_file(p))
                   for p in images if p.is_file()]
        if not records:
            raise ValueError("Ingest produced no prepared images")
        result["image_inventory"] = hashlib.sha256(json.dumps(records).encode()).hexdigest()
    if stage == "train":
        # Existence and size checks are insufficient for a full-SH model: a
        # truncated binary can still have a nonzero size.  Validate the master
        # before allowing the stage to be reused on resume.  The result shape
        # stays compatible with historical pipeline records.
        from .ply import verify_splat_ply

        verify_splat_ply(root / "model/scene.ply", require_nonempty=True)
    if stage == "export" and (root / "model/scene.splat").stat().st_size % 32:
        raise ValueError("Viewer splat has incomplete records")
    if stage == "package":
        from .package import verify_package
        ok, problems = verify_package(root / "archive")
        if not ok:
            raise ValueError("Archive verification failed: " + "; ".join(problems[:5]))
    return result


def restore_args(args):
    if getattr(args, "resume", False):
        saved = read_json(Path(args.run_dir) / "pipeline.json")
        if not saved or saved.get("schema") != "vitrine/pipeline/1":
            raise ValueError("No resumable pipeline record. Use individual stages for a historical run.")
        for key, value in saved["config"].items():
            if not key.startswith("_"):
                setattr(args, key, value)
    return args


def run_pipeline(args, stages):
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "pipeline.json"
    with run_lock(run_dir):
        saved = read_json(path)
        resume = bool(getattr(args, "resume", False))
        if saved and not resume:
            raise ValueError("This run already has pipeline state. Use run --resume or choose a new --run-dir.")
        if not saved and any((run_dir / p).exists() for p in ("ingest/ingest.json", "model/scene.ply", "archive")):
            raise ValueError("Existing outputs belong to a historical run; use individual stages or a new run directory.")
        config = {k: v for k, v in vars(args).items() if k not in {"func", "resume", "verbose", "command", "run_dir"}
                  and not k.startswith("_")}
        state = saved or dict(
            schema="vitrine/pipeline/1",
            config=config,
            stages={},
            created=time.time(),
            compatibility=current_compatibility(),
            source_identity_schema="sha256-content-v2",
        )
        if saved:
            _recover_running_stages(state, run_dir)
            incompatibility = _invalidate_incompatible_stages(state)
            if incompatibility:
                state.setdefault("recovery", []).append({
                    "kind": "compatibility_refused",
                    "reason": incompatibility,
                    "at": time.time(),
                })
                atomic_json(path, state)
                raise ValueError(
                    f"Cannot safely resume this pipeline: {incompatibility}. "
                    "Choose a new --run-dir; existing outputs were preserved."
                )
        (run_dir / "cancel.request").unlink(missing_ok=True)
        state.update(state="running", pid=os.getpid(), updated=time.time(), error=None)
        atomic_json(path, state)
        try:
            from .preflight import check_preflight, summary
            pending = {name for name, _ in stages if state["stages"].get(name, {}).get("state") != "complete"}
            # Session import was already validated and staged by cmd_run.
            source = Path(args.source)
            identity = source_identity(source)
            if (saved and saved.get("source_identity_schema") == "sha256-content-v2"
                    and saved.get("source_identity") != identity):
                raise ValueError("Input media changed since this run began; choose a new run directory")
            state["source_identity"] = identity
            state["source_identity_schema"] = "sha256-content-v2"
            from .hardware import resolve_runtime
            from .profiles import describe, resolve
            runtime = resolve_runtime()
            atomic_json(run_dir / "runtime.json", runtime)
            state["runtime"] = runtime
            state["live_previews"] = os.environ.get("VITRINE_LIVE_PREVIEWS", "1") != "0"
            profile = resolve(args.quality, args.tier)
            if getattr(args, "iterations", None):
                from dataclasses import replace
                profile = replace(profile, iterations=args.iterations, measured_runtime_minutes=None)
            state["profile"] = describe(profile, hardware=runtime["hardware"])
            with Progress(run_dir, "preflight"), measure(run_dir, "preflight"):
                report = check_preflight(source if "ingest" in pending else None, run_dir,
                                         require_gpu=bool({"train", "evaluate"} & pending) or ("sfm" in pending and args.gpu != "no"),
                                         check_sfm="sfm" in pending, check_training=bool({"train", "evaluate"} & pending),
                                         check_media="ingest" in pending, sfm_gpu=args.gpu != "no")
                atomic_json(run_dir / "preflight.json", report)
                print(summary(report), flush=True)
                if not report["ready"]:
                    raise RuntimeError("Preflight failed. See preflight.json; correct the listed problems and resume.")
            for name, stage in stages:
                check_cancel(run_dir)
                previous = state["stages"].get(name, {})
                if previous.get("state") == "complete":
                    if fingerprint(run_dir, name) != previous.get("outputs"):
                        raise ValueError(f"Completed {name} outputs changed; refusing to silently reuse or overwrite them")
                    print(f"Keeping completed stage: {name}", flush=True)
                    continue
                began = time.monotonic()
                state["stages"][name] = dict(
                    state="running", started=time.time(), heartbeat=time.time(), pid=os.getpid()
                )
                atomic_json(path, state)
                stage_exit_code = None
                try:
                    with Progress(run_dir, name), measure(run_dir, name):
                        code = stage(args)
                        stage_exit_code = code
                        if code:
                            raise RuntimeError(f"{name} exited with code {code}")
                        outputs = fingerprint(run_dir, name)
                    state["stages"][name].update(
                        state="complete", seconds=round(time.monotonic()-began, 3), outputs=outputs,
                        exit_code=stage_exit_code, heartbeat=time.time(), finished=time.time(), pid=None,
                    )
                except BaseException as exc:
                    state["stages"][name].update(
                        state="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                        seconds=round(time.monotonic()-began, 3), error=str(exc),
                        exit_code=stage_exit_code, heartbeat=time.time(), finished=time.time(), pid=None,
                    )
                    raise
                finally:
                    state["updated"] = time.time()
                    atomic_json(path, state)
            state["state"] = "complete"
        except BaseException as exc:
            state.update(state="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed", error=str(exc))
            raise
        finally:
            state.update(updated=time.time(), pid=None)
            atomic_json(path, state)
            events = timing_summary(run_dir)
            atomic_json(run_dir / "timing-summary.json", dict(state=state["state"], events=events))
            print("\nPIPELINE SUMMARY", flush=True)
            for name, record in state["stages"].items():
                print(f"{name.upper():<20} {record.get('seconds', 0):8.1f}s  {record['state']}", flush=True)
            print(f"Status: {state['state']}. Reports: {run_dir / 'pipeline.json'}", flush=True)
    return 0
