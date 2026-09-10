"""Exercise liveness queries against a real worker, including Windows."""
import subprocess
import sys

from vitrine.construction import process_alive
from vitrine.serve import _pid_is_running


def test_status_queries_do_not_terminate_worker():
    worker = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
    )
    try:
        for _ in range(100):
            assert process_alive(worker.pid) is True
            assert _pid_is_running(worker.pid)
            assert worker.poll() is None
    finally:
        worker.communicate(timeout=10)
    assert worker.returncode == 0
    assert process_alive(worker.pid) is False
    assert not _pid_is_running(worker.pid)
