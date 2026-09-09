"""COLMAP observation and conversion of closed mapper snapshots."""
from __future__ import annotations

import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from .construction import SnapshotStore, atomic_json, sparse_payload


def docker_observation(command, timeout, check=False):
    """Do not leave a converter container alive if its client times out."""
    name = "vitrine-preview-" + uuid.uuid4().hex
    command = [*command[:2], "--name", name, *command[2:]]
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=check)
    except subprocess.TimeoutExpired:
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise


def snapshot_options(image):
    try:
        result = docker_observation(["docker", "run", "--rm", image, "colmap", "mapper", "-h"], 30)
        help_text = result.stdout + result.stderr
        for name in ("snapshot_frames_freq", "snapshot_images_freq"):
            if "Mapper." + name in help_text and "Mapper.snapshot_path" in help_text:
                return ["--Mapper.snapshot_path", "/work/construction/raw",
                        "--Mapper." + name, "5"]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return []


def observe_line(progress, line):
    # Do not turn registered-view counts into a percentage: some input images
    # may never register. Unknown COLMAP versions still get truthful activity.
    values = {"message": line.strip()[-300:]}
    extracted = re.search(r"Processed file \[(\d+)/(\d+)\]", line)
    if extracted:
        values.update(count=int(extracted[1]), total=int(extracted[2]), unit="images")
    sequential = re.search(r"Processing image \[(\d+)/(\d+)\]", line)
    if sequential:
        values.update(count=int(sequential[1]), total=int(sequential[2]), unit="video frames")
    matching = re.search(r"(?:Matching|Processing) block \[(\d+)/(\d+),\s*(\d+)/(\d+)\]", line)
    if matching:
        values.update(count=(int(matching[1])-1)*int(matching[4])+int(matching[3]),
                      total=int(matching[2])*int(matching[4]), unit="blocks")
    now = time.monotonic()
    # Keep draining the pipe even when COLMAP logs faster than the UI polls.
    if extracted or sequential or matching or now - getattr(progress, "last_line", 0) >= 1:
        progress.update(**values)
        progress.last_line = now


class MapperSnapshots:
    """Only convert predecessors of the newest snapshot while mapper runs.

    COLMAP writes each snapshot synchronously into a fresh timestamp directory.
    A successor therefore establishes the predecessor is closed. After mapper
    exits, all snapshots are closed. Merely stable file sizes are insufficient.
    """

    def __init__(self, work, image, progress):
        self.work, self.image, self.progress = Path(work), image, progress
        self.raw = self.work / "construction/raw"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.seen = set()
        self.primary_registered = 0
        self.primary_points = 0

    def start(self):
        self.thread.start()

    def _watch(self):
        while not self.stop.wait(5):
            self.scan(False)

    def scan(self, finished):
        try:
            candidates = sorted(p for p in self.raw.iterdir() if p.is_dir() and not p.is_symlink()) if self.raw.exists() else []
            closed = candidates if finished else candidates[:-1]
            # Coalesce obsolete observations instead of accumulating conversion work.
            pending = [p for p in closed if p.name not in self.seen]
            if not pending:
                return
            source = pending[-1]
            text_dir = self.work / "construction/converted" / source.name
            text_dir.mkdir(parents=True, exist_ok=True)
            command = ["docker", "run", "--rm"]
            import os
            if hasattr(os, "getuid"):
                command += ["--user", f"{os.getuid()}:{os.getgid()}"]
            command += ["-v", f"{self.work.resolve()}:/work", self.image, "colmap", "model_converter",
                        "--input_path", "/work/construction/raw/" + source.name,
                        "--output_path", "/work/construction/converted/" + source.name,
                        "--output_type", "TXT"]
            docker_observation(command, timeout=45, check=True)
            from .colmap_io import read_model
            model = read_model(text_dir)
            if not model.images or not len(model.points_xyz):
                raise ValueError("Snapshot has no registered geometry yet")
            # A subsequent small disconnected component must not replace the
            # room in the live viewer while multiple-model search continues.
            if len(model.images) >= self.primary_registered:
                payload = sparse_payload(model)
                SnapshotStore(self.work).publish("sparse", ".json", lambda p: atomic_json(p, payload),
                                                 registered=len(model.images), points=len(model.points_xyz))
                self.primary_registered = len(model.images)
                self.primary_points = len(model.points_xyz)
            self.progress.update(registered=self.primary_registered, points=self.primary_points,
                                 candidate_registered=len(model.images), preview_error=None)
            self.seen.update(p.name for p in pending)
            # These are exclusively generated preview intermediates, all closed.
            # Validate the resolved parent before any recursive removal.
            import shutil
            for path in [*pending, text_dir]:
                expected = self.raw if path in pending else self.work / "construction/converted"
                if not path.is_symlink() and path.resolve().parent == expected.resolve():
                    shutil.rmtree(path)
        except Exception as exc:
            self.progress.update(preview_error=str(exc))

    def close(self):
        self.stop.set()
        self.thread.join()
        self.scan(True)
