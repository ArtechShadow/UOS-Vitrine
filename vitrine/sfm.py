"""Structure from motion — recover camera poses with COLMAP.

COLMAP is not packaged for CachyOS, so it runs from the official
``colmap/colmap:latest`` container. That image is COLMAP **4.x**, which uses
the ``FeatureExtraction.*`` / ``FeatureMatching.*`` option names rather than
the older ``SiftExtraction.*`` / ``SiftMatching.*`` ones.

Four things here exist specifically to serve reconstruction quality, and each
one corrects a failure mode that produces a *plausible but wrong* result
rather than an error:

**One camera model per folder.** ``ingest`` stages each camera group into its
own directory and we pass ``--ImageReader.single_camera_per_folder``. Using
``--ImageReader.single_camera`` instead would fit one set of intrinsics across
a 25 MP phone still and a 720p video frame, quietly warping everything.

**EXIF is preserved upstream** so COLMAP can seed focal length from the
camera's own metadata instead of guessing ``1.2 * max(width, height)``.

**Exhaustive matching for modest image counts.** In an enclosed room, loop
closure is what stops the reconstruction drifting apart; sequential matching
alone will not find that the last frame looks at the same wall as the first.
Below ``EXHAUSTIVE_LIMIT`` images the O(n^2) cost is worth paying.

**A second bundle adjustment** with principal-point refinement, which the
default mapper leaves fixed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_IMAGE = "colmap/colmap:latest"

#: Above this many images, exhaustive matching stops being worth the wall
#: clock and we fall back to sequential + loop detection.
EXHAUSTIVE_LIMIT = 300


class ColmapError(RuntimeError):
    pass


@dataclass
class SfmResult:
    sparse_dir: Path       # text model — cameras.txt / images.txt / points3D.txt
    database: Path
    images_dir: Path
    registered_images: int
    cameras: int
    points: int
    used_gpu: bool

    def describe(self) -> dict[str, object]:
        return {
            "registered_images": self.registered_images,
            "cameras": self.cameras,
            "points": self.points,
            "used_gpu": self.used_gpu,
            "sparse_dir": str(self.sparse_dir),
        }


def gpu_available(image: str = DEFAULT_IMAGE) -> bool:
    """Can we actually pass the GPU into a container?

    Probed rather than assumed. ``nvidia-container-toolkit`` being installed is
    not sufficient — the CDI spec pins exact driver library versions, so a
    partial driver upgrade leaves it referencing files that no longer exist and
    every GPU container fails to start. Falling back to CPU SIFT is slow but
    correct; failing the run outright is not.
    """
    try:
        probe = subprocess.run(
            ["docker", "run", "--rm", "--pull=never", "--gpus", "all", image, "nvidia-smi", "-L"],
            capture_output=True, text=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("COLMAP GPU probe failed; CPU camera solving would be slower: %s", exc)
        return False
    if probe.returncode == 0:
        return True
    detail = (probe.stderr or probe.stdout).strip().splitlines()
    logger.warning(
        "GPU passthrough unavailable, falling back to CPU: %s",
        detail[0][:200] if detail else "unknown error",
    )
    return False


def _run(
    args: list[str],
    *,
    mounts: dict[Path, str],
    image: str,
    use_gpu: bool,
    timeout: int,
    log_path: Path | None = None,
    progress=None,
) -> None:
    """Run one COLMAP subcommand in the container.

    The container runs as the invoking user so the outputs are not left
    root-owned — a genuine nuisance otherwise, since cleanup then needs sudo.
    Windows has no UID/GID concept and ``os.getuid`` doesn't exist there;
    Docker Desktop's WSL2 backend maps bind-mount ownership on its own, so
    ``--user`` is a Linux-host-Docker-only concern.
    """
    command = ["docker", "run", "--rm"]
    if hasattr(os, "getuid"):
        command += ["--user", f"{os.getuid()}:{os.getgid()}"]
    if use_gpu:
        command += ["--gpus", "all"]
    for host_path, container_path in mounts.items():
        command += ["-v", f"{host_path.resolve()}:{container_path}"]
    command += [image, "colmap", *args]

    import threading
    import uuid
    from collections import deque
    from .construction import Progress
    from .sfm_progress import MapperSnapshots, observe_line, snapshot_options

    work = next(host for host, mounted in mounts.items() if mounted == "/work")
    mapper = args[0] == "mapper"
    options = snapshot_options(image) if mapper and os.environ.get("VITRINE_LIVE_PREVIEWS", "1") != "0" else []
    if options:
        (work / "construction/raw").mkdir(parents=True, exist_ok=True)
        command += options
    container = "vitrine-" + uuid.uuid4().hex
    command[2:2] = ["--name", container]
    logger.info("colmap %s", args[0])
    from contextlib import nullcontext
    with (nullcontext(progress) if progress else Progress(work, "sfm")) as progress:
        progress.update(substage=args[0], message="Starting " + args[0],
                        count=None, total=None, unit=None,
                        preview_error=("Live geometry previews are disabled for this run" if os.environ.get("VITRINE_LIVE_PREVIEWS", "1") == "0"
                                       else "COLMAP mapper snapshot discovery failed or snapshots are unsupported") if mapper and not options else None)
        monitor = MapperSnapshots(work, image, progress) if options else None
        tail = deque(maxlen=15)
        result = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, encoding="utf-8", errors="replace", bufsize=1)
        def drain():
            handle = None
            try:
                try:
                    handle = log_path.open("a", encoding="utf-8") if log_path else None
                    if handle:
                        handle.write("\n$ " + " ".join(command) + "\n")
                except OSError:
                    if handle:
                        handle.close()
                    handle = None
                for line in result.stdout:
                    tail.append(line.rstrip())
                    if handle:
                        try:
                            handle.write(line)
                            handle.flush()
                        except OSError:
                            handle.close()
                            handle = None
                    observe_line(progress, line)
            finally:
                if handle:
                    handle.close()
        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        if monitor:
            monitor.start()
        try:
            from .pipeline import check_cancel
            deadline = time.monotonic() + timeout
            while result.poll() is None:
                check_cancel(work.parent)
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    result.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
            code = result.returncode
            reader.join()
            if code:
                raise ColmapError(f"colmap {args[0]} failed:\n" + "\n".join(tail))
        finally:
            if result.poll() is None:
                # Killing the docker client alone leaves the GPU container running.
                try:
                    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=15)
                finally:
                    result.kill()
                    result.wait()
            reader.join(timeout=5)
            if not reader.is_alive():
                result.stdout.close()
            if monitor:
                monitor.close()


def _count_model(sparse_dir: Path) -> tuple[int, int, int]:
    """(images, cameras, points) from a text model, without a full parse."""

    def data_lines(name: str) -> int:
        path = sparse_dir / name
        if not path.is_file():
            return 0
        with path.open(encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip() and not line.startswith("#"))

    # images.txt uses two lines per image (pose + observations).
    return (data_lines("images.txt") + 1) // 2, data_lines("cameras.txt"), data_lines("points3D.txt")


def run_sfm(
    images_dir: Path,
    work_dir: Path,
    *,
    max_image_size: int,
    image: str = DEFAULT_IMAGE,
    use_gpu: bool | None = None,
    camera_model: str = "OPENCV",
    matching: str = "auto",
) -> SfmResult:
    from .construction import Progress
    with Progress(work_dir, "sfm") as observer:
        return _run_sfm(images_dir, work_dir, max_image_size=max_image_size,
                        image=image, use_gpu=use_gpu, camera_model=camera_model, observer=observer, matching=matching)


def _run_sfm(
    images_dir: Path,
    work_dir: Path,
    *,
    max_image_size: int,
    image: str = DEFAULT_IMAGE,
    use_gpu: bool | None = None,
    camera_model: str = "OPENCV",
    observer=None,
    matching: str = "auto",
) -> SfmResult:
    """Full SfM: features → matching → mapping → bundle adjustment → text model.

    ``images_dir`` must contain one subdirectory per camera group, as produced
    by ``ingest``.
    """
    from .telemetry import measure
    def run_command(arguments, **kwargs):
        with measure(work_dir, "colmap_" + arguments[0], used_gpu=kwargs.get("use_gpu")):
            return _run(arguments, progress=observer, **kwargs)
    images_dir = Path(images_dir).resolve()
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    groups = sorted(p for p in images_dir.iterdir() if p.is_dir())
    if not groups:
        raise ColmapError(
            f"{images_dir} has no camera-group subdirectories. Run ingest first — "
            "COLMAP is told about multiple cameras via the folder layout."
        )
    n_images = sum(
        1 for p in images_dir.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}
    )
    logger.info(
        "SfM over %d images in %d camera group(s): %s",
        n_images, len(groups), ", ".join(g.name for g in groups),
    )

    if use_gpu is None:
        use_gpu = gpu_available(image)
    if not use_gpu:
        logger.warning("COLMAP is using CPU camera solving; expect a substantially longer runtime")

    log_path = work_dir / "colmap.log"
    database = work_dir / "database.db"
    sparse_root = work_dir / "sparse"
    sparse_root.mkdir(exist_ok=True)

    # Container-side paths. Mounting to fixed locations keeps the command
    # lines readable and independent of where the run lives on the host.
    mounts = {images_dir: "/images", work_dir: "/work"}
    gpu_flag = "1" if use_gpu else "0"
    from .hardware import resolve_runtime
    workers = str(resolve_runtime()["workers"]["sfm"])

    run_command(
        [
            "feature_extractor",
            "--database_path", "/work/database.db",
            "--image_path", "/images",
            # The whole point: one intrinsic model per camera group.
            "--ImageReader.single_camera_per_folder", "1",
            "--ImageReader.camera_model", camera_model,
            "--FeatureExtraction.max_image_size", str(int(max_image_size)),
            "--FeatureExtraction.use_gpu", gpu_flag,
            "--FeatureExtraction.num_threads", workers,
        ],
        mounts=mounts, image=image, use_gpu=use_gpu, timeout=7200, log_path=log_path,
    )

    if matching not in {"auto", "exhaustive", "sequential"}:
        raise ValueError(f"Unknown matching mode: {matching}")
    if matching == "exhaustive" or (matching == "auto" and n_images <= EXHAUSTIVE_LIMIT):
        logger.info("exhaustive matching (%d images) — best loop closure for interiors", n_images)
        match_args = [
            "exhaustive_matcher",
            "--database_path", "/work/database.db",
            "--FeatureMatching.use_gpu", gpu_flag,
        ]
    else:
        logger.info("sequential matching with loop detection (%d images)", n_images)
        match_args = [
            "sequential_matcher",
            "--database_path", "/work/database.db",
            "--SequentialMatching.overlap", "15",
            "--SequentialMatching.loop_detection", "1",
            "--FeatureMatching.use_gpu", gpu_flag,
        ]
    run_command(match_args, mounts=mounts, image=image, use_gpu=use_gpu, timeout=14400, log_path=log_path)

    run_command(
        [
            "mapper",
            "--database_path", "/work/database.db",
            "--image_path", "/images",
            "--output_path", "/work/sparse",
            "--Mapper.multiple_models", "1",
            "--Mapper.max_num_models", "10",
            "--Mapper.num_threads", workers,
        ],
        mounts=mounts, image=image, use_gpu=False, timeout=14400, log_path=log_path,
    )

    model_dir = select_largest_model(sparse_root)
    container_model = "/work/sparse/" + model_dir.name

    # The mapper holds the principal point fixed. Refining it in a second pass
    # is cheap and measurably improves reprojection on phone cameras.
    run_command(
        [
            "bundle_adjuster",
            "--input_path", container_model,
            "--output_path", container_model,
            "--BundleAdjustment.refine_principal_point", "1",
        ],
        mounts=mounts, image=image, use_gpu=False, timeout=7200, log_path=log_path,
    )

    # Convert to text. This feeds colmap_io *and* is the archival copy the
    # preservation package deposits — produced by the normal path, not by a
    # separate export step someone has to remember to run.
    text_dir = work_dir / "sparse_text"
    text_dir.mkdir(exist_ok=True)
    run_command(
        [
            "model_converter",
            "--input_path", container_model,
            "--output_path", "/work/sparse_text",
            "--output_type", "TXT",
        ],
        mounts=mounts, image=image, use_gpu=False, timeout=1800, log_path=log_path,
    )

    try:
        from .construction import SnapshotStore, atomic_json, sparse_payload
        from .colmap_io import read_model
        sparse = read_model(text_dir)
        payload = sparse_payload(sparse)
        SnapshotStore(work_dir).publish("sparse", ".json", lambda p: atomic_json(p, payload),
                                        registered=len(sparse.images), points=len(sparse.points_xyz), final=True)
    except Exception as exc:
        logger.warning("Final camera preview unavailable: %s", exc)

    registered, cameras, points = _count_model(text_dir)
    if registered == 0:
        raise ColmapError(f"COLMAP registered no images. See {log_path}.")

    logger.info(
        "SfM complete: %d/%d images registered, %d camera model(s), %d points",
        registered, n_images, cameras, points,
    )
    if cameras < len(groups):
        logger.warning(
            "expected %d camera models (one per group) but got %d — "
            "single_camera_per_folder may not have taken effect",
            len(groups), cameras,
        )

    result = SfmResult(
        sparse_dir=text_dir,
        database=database,
        images_dir=images_dir,
        registered_images=registered,
        cameras=cameras,
        points=points,
        used_gpu=use_gpu,
    )
    (work_dir / "sfm.json").write_text(json.dumps(result.describe(), indent=2), encoding="utf-8")
    validate_registration(registered, n_images)
    return result


def validate_registration(registered: int, total: int) -> None:
    """Reject a partial camera solve before spending time on splat training."""
    if registered < 3 or registered < total * 0.6:
        raise ColmapError(
            f"Camera alignment incomplete: only {registered}/{total} images registered "
            f"({registered / max(total, 1):.1%}). Training stopped: at least three views "
            "and 60% camera coverage are required. Inspect the camera model, image "
            "overlap and blur; preserve this attempt and retry alignment."
        )


def select_largest_model(sparse_root: Path) -> Path:
    """COLMAP's first component may be a two-view fragment; preserve all models."""
    import struct
    candidates = []
    for path in sorted(Path(sparse_root).glob('*/images.bin')):
        with path.open('rb') as handle:
            header = handle.read(8)
        if len(header) == 8:
            candidates.append((struct.unpack('<Q', header)[0], path.parent))
    if not candidates:
        raise ColmapError(
            "COLMAP mapper produced no readable model. Check overlap, motion blur "
            f"and texture in the input; see {Path(sparse_root).parent / 'colmap.log'}."
        )
    registered, chosen = max(candidates, key=lambda item: item[0])
    logger.info("Selected camera component %s with %d registered images from %d models",
                chosen.name, registered, len(candidates))
    return chosen
