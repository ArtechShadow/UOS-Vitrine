"""Small durable stage measurements; unavailable counters stay null."""
from __future__ import annotations

import json
import logging
import os
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


@contextmanager
def measure(folder: Path, stage: str, **counts):
    """Append one event even when a stage raises; never hide the stage error."""
    started, cpu = time.perf_counter(), time.process_time()
    record = {"stage": stage, "started": time.time(), "pid": os.getpid(),
              "gpu_before": gpu_memory(), **counts}
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
                      gpu_after=gpu_memory())
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
