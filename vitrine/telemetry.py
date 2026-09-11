"""Small durable stage measurements; unavailable counters stay null."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)
_lock = threading.Lock()


def gpu_memory():
    torch = sys.modules.get("torch")
    try:
        if torch is not None and torch.cuda.is_initialized():
            free, total = torch.cuda.mem_get_info()
            return {"allocated_bytes": torch.cuda.memory_allocated(),
                    "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
                    "free_bytes": free, "total_bytes": total}
    except (RuntimeError, AssertionError):
        pass
    return None


def process_memory():
    """Best-effort process memory diagnostics with no new dependency.

    ``ru_maxrss`` is a peak value and has different units on POSIX and
    Windows.  Linux additionally exposes the current resident set in
    ``/proc``.  The fields remain ``None`` when the host does not expose them;
    telemetry must never make a reconstruction fail just because diagnostics
    are unavailable.
    """
    current = None
    peak = None
    try:
        import resource

        value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        peak = value if os.name == "nt" else value * 1024
    except (ImportError, OSError, ValueError):
        pass
    if os.name != "nt":
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
            current = resident_pages * page_size
        except (AttributeError, OSError, IndexError, ValueError):
            pass
    return {"current_rss_bytes": current, "peak_rss_bytes": peak}


def disk_space(folder):
    """Best-effort free/total bytes for the filesystem containing ``folder``."""
    try:
        usage = shutil.disk_usage(Path(folder))
    except OSError:
        return None
    return {"free_bytes": usage.free, "total_bytes": usage.total}


@contextmanager
def run_lock(run_dir):
    """Compatibility lock for task-owned workers and future sidecars.

    The pipeline's public ``run_lock`` remains the canonical implementation;
    this lazy delegation keeps callers that historically imported the helper
    from telemetry on the same process-wide lock semantics without creating an
    import cycle during module initialisation.
    """
    from .pipeline import run_lock as pipeline_run_lock

    with pipeline_run_lock(run_dir):
        yield


@contextmanager
def measure(folder: Path, stage: str, **counts):
    """Append one event even when a stage raises; never hide the stage error."""
    started, cpu = time.perf_counter(), time.process_time()
    record = {"stage": stage, "started": time.time(), "pid": os.getpid(),
              "gpu_before": gpu_memory(), "memory_before": process_memory(),
              "disk_before": disk_space(folder), **counts}
    try:
        yield record
    except BaseException as exc:
        record.update(state="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                      error=f"{type(exc).__name__}: {exc}")
        raise
    else:
        record["state"] = "complete"
    finally:
        elapsed = time.perf_counter() - started
        record.update(seconds=round(elapsed, 4), cpu_seconds=round(time.process_time()-cpu, 4),
                     gpu_after=gpu_memory(), memory_after=process_memory(),
                     disk_after=disk_space(folder))
        # CPU time is for this Python process, not its COLMAP/ffmpeg children.
        record["cpu_scope"] = "python-process"
        try:
            path = Path(folder) / "timings.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with _lock, path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, allow_nan=False) + "\n")
        except (OSError, ValueError):
            logger.warning("Could not persist stage timing for %s", stage, exc_info=True)


def timing_summary(run_dir: Path):
    events = []
    for folder in (Path(run_dir), Path(run_dir)/"ingest", Path(run_dir)/"sfm", Path(run_dir)/"model",
                   Path(run_dir)/"objects", Path(run_dir)/"object-meshes"):
        path = folder / "timings.jsonl"
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    logger.warning("Incomplete timing record in %s", path)
    return events
