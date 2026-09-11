"""Command line interface.

Stages can be run individually or as one ``run`` command. Each writes its
outputs and a small JSON report into ``runs/<name>/``, so a failed or
interrupted stage can be re-run without repeating the ones before it — which
matters when structure-from-motion takes an hour and training takes two.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from . import profiles

logger = logging.getLogger("vitrine")


def _setup_logging(verbose: bool, log_file: Path | None = None) -> None:
    """Console logging, plus a per-run file so the UI's log tail has something to show.

    Without a FileHandler here, ``vitrine ui``'s train/SfM log viewer always
    reads an empty result — the endpoint has always looked for
    ``<run-dir>/logs/vitrine.log``, but nothing ever wrote it.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("PIL").setLevel(logging.WARNING)


def _run_dir(args: argparse.Namespace) -> Path:
    path = Path(args.run_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


# --- doctor -----------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    """Report whether this machine can actually run the pipeline."""
    from . import cuda_toolkit

    print("vitrine environment check\n" + "=" * 46)

    status = cuda_toolkit.status()
    print(f"  CUDA toolkit   : {status['cuda_root'] or 'NOT FOUND'}")
    print(f"  host compiler  : {status['host_compiler'] or 'NOT FOUND'}")
    print(f"  target arch    : {status['arch_list']}")
    print(f"  gsplat prebuilt: {'yes' if status['gsplat_prebuilt'] else 'no (will compile on first use)'}")

    ok = True
    if not status["cuda_root"]:
        ok = False
        print("\n  ! No nvcc. Install the CUDA wheels:  pip install nvidia-cuda-nvcc")
    if not status["host_compiler"]:
        ok = False
        if cuda_toolkit.IS_WINDOWS:
            print(
                "\n  ! Microsoft C++ Build Tools (MSVC) not found.\n"
                "    Install Visual Studio Build Tools with the Desktop development with C++ workload."
            )
        else:
            print(
                "\n  ! No GCC <= 15 found. CUDA 13's nvcc cannot compile against GCC 16\n"
                "    headers and will die with 'cudafe++ ... signal 11'.\n"
                "    Fix:  sudo pacman -S gcc15\n"
                "    Or:   export VITRINE_GCC_BIN=/path/to/dir/containing/gcc-15"
            )

    try:
        import torch

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_memory / 2**30
            print(f"\n  GPU            : {name} ({vram:.1f} GB)")
        else:
            ok = False
            print("\n  ! torch reports no CUDA device")
    except ImportError:
        ok = False
        print("\n  ! torch not installed")

    tier = profiles.detect_tier()
    profile = profiles.resolve(args.quality, tier)
    from .hardware import detect_hardware
    estimate = profile.estimated_minutes(hardware=detect_hardware())
    print(f"  profile        : {profile.name} (" + (f"reference {estimate:.1f} min" if estimate is not None else "runtime not measured on this GPU") + ")")

    import shutil as _shutil

    for tool in ("docker", "ffmpeg"):
        found = _shutil.which(tool)
        print(f"  {tool:<15}: {found or 'NOT FOUND'}")
        if not found:
            ok = False

    if _shutil.which("docker"):
        from .sfm import gpu_available

        from .windows_tools import docker_engine_ready
        if not docker_engine_ready():
            ok = False
            print("  docker engine  : unavailable — start Docker Desktop with Linux containers; finish WSL setup/restart if required")
        else:
            print(f"  docker GPU     : {'yes' if gpu_available() else 'no — COLMAP will use CPU (much slower)'}")

    print("\n" + ("All good." if ok else "Problems found — see the notes above."))
    return 0 if ok else 1


# --- stages -----------------------------------------------------------------

def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import ingest

    run_dir = _run_dir(args)
    profile = profiles.resolve(args.quality, args.tier)
    source = Path(args.source)
    session_path = getattr(args, "session", None)
    if session_path:
        from .capture_session import import_session

        session = import_session(Path(session_path), run_dir)
        source = run_dir / "source"
        print(f"session {session.session_id}  {session.title}")
        print(f"  {len(session.stills)} stills  {len(session.videos)} video")
        for warning in session.warnings:
            print(f"  warning: {warning}")
    report = ingest(
        source,
        run_dir / "ingest",
        long_edge=profile.colmap_long_edge,
        stills_budget=args.stills_budget,
        video_budget=args.video_budget,
        selection_preset=getattr(args, "selection_preset", "balanced"),
        include=args.include,
    )
    print(f"\n{report.accepted} images staged, {report.rejected} rejected")
    for note in report.notes:
        print(f"  note: {note}")
    return 0


def cmd_sfm(args: argparse.Namespace) -> int:
    from .sfm import run_sfm

    run_dir = _run_dir(args)
    profile = profiles.resolve(args.quality, args.tier)
    result = run_sfm(
        run_dir / "ingest" / "images",
        run_dir / "sfm",
        max_image_size=profile.colmap_long_edge,
        use_gpu=None if args.gpu == "auto" else args.gpu == "yes",
        matching=getattr(args, "matching", "auto"),
    )
    print(f"\n{result.registered_images} images registered · "
          f"{result.cameras} camera model(s) · {result.points:,} points")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from .colmap_io import read_model
    from .engines import get_engine

    run_dir = _run_dir(args)
    profile = profiles.resolve(args.quality, args.tier)
    if args.iterations:
        from dataclasses import replace

        profile = replace(profile, iterations=args.iterations)

    model = read_model(run_dir / "sfm" / "sparse_text")
    from .sfm import validate_registration
    image_root = run_dir / "ingest" / "images"
    total_images = sum(1 for p in image_root.rglob("*")
                       if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
    validate_registration(len(model.images), total_images)
    report = get_engine().train(
        model,
        run_dir / "ingest" / "images",
        run_dir / "model",
        profile,
        eval_every=args.eval_every,
    )
    print(f"\nPSNR {report.final_psnr} dB · SSIM {report.final_ssim} · "
          f"{report.n_gaussians:,} Gaussians · {report.minutes} min")
    return 0


def cmd_preflight(args):
    from .preflight import check_preflight, summary
    from .construction import atomic_json
    report = check_preflight(Path(args.source) if args.source else None, Path(args.run_dir),
                             require_gpu=True, sfm_gpu=args.gpu != "no")
    atomic_json(Path(args.run_dir) / "preflight.json", report)
    print(summary(report))
    return 0 if report["ready"] else 1


def cmd_export(args):
    from .engines import get_engine
    run_dir = _run_dir(args)
    engine = get_engine()
    engine.validate(run_dir / "model/scene.ply")
    output = engine.export(run_dir / "model/scene.ply", run_dir / "model/scene.splat")
    print(f"Viewer ready: {output}")
    return 0


def cmd_cleanup(args):
    from .engines import get_engine
    run_dir = _run_dir(args)
    get_engine().cleanup(run_dir / "model/scene.ply", run_dir / "model/scene.cleaned.ply")
    print("Cleanup candidate saved separately; evaluate it against held-out views before selecting it.")
    return 0


def cmd_mesh_images(args):
    from .engines import LocalMeshEngine
    command = json.loads(os.environ.get("VITRINE_MESH_COMMAND_JSON", "[]"))
    weights = json.loads(os.environ.get("VITRINE_MESH_WEIGHTS_JSON", "[]"))
    engine = LocalMeshEngine(command, weights)
    print(engine.reconstruct(Path(args.input), Path(args.output), timeout=args.timeout))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Measure a trained PLY against held-out views, broken down by camera group."""
    from .colmap_io import read_model
    from .dataset import ViewSet
    from .evaluate import evaluate_ply

    run_dir = _run_dir(args)
    profile = profiles.resolve(args.quality, args.tier)
    model = read_model(run_dir / "sfm" / "sparse_text")
    views = ViewSet(model, run_dir / "ingest" / "images", long_edge=profile.source_long_edge)

    ply = Path(args.ply) if getattr(args, "ply", None) else run_dir / "model" / "scene.ply"
    from .construction import Progress
    from .evaluation_preview import EvaluationPreview
    with Progress(run_dir / "model", "evaluate") as observer:
        preview = EvaluationPreview(run_dir / "model/evaluation-previews", observer)
        report = evaluate_ply(ply, views, on_view=preview)
    print("\n" + report.summary())
    (run_dir / "model" / "evaluation.json").write_text(report.to_json(), encoding="utf-8")
    return 0


def cmd_package(args: argparse.Namespace) -> int:
    from .package import build_package

    run_dir = _run_dir(args)

    def load(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    train_report = load(run_dir / "model" / "train.json")
    profile = profiles.resolve(args.quality, args.tier)
    if getattr(args, "iterations", None):
        from dataclasses import replace
        profile = replace(profile, iterations=args.iterations, measured_runtime_minutes=None)

    session_path = run_dir / "capture-session.json"
    result = build_package(
        run_dir / "archive",
        originals=[Path(p) for p in args.originals],
        sfm_dir=run_dir / "sfm" / "sparse_text",
        model_ply=run_dir / "model" / "scene.ply",
        derivatives=[run_dir / "model/scene.splat"] if (run_dir / "model/scene.splat").is_file() else None,
        database=run_dir / "sfm" / "database.db",
        title=args.title,
        subject=args.subject,
        capture_type=getattr(args, "capture_type", None) or load(run_dir / "capture.json").get("capture_type", "scene"),
        train_report=train_report,
        ingest_report=load(run_dir / "ingest" / "ingest.json"),
        sfm_report=load(run_dir / "sfm" / "sfm.json"),
        profile=profiles.describe(profile, hardware=load(run_dir / "runtime.json").get("hardware", {})),
        objects_dir=run_dir / "objects",
        object_meshes_dir=run_dir / "object-meshes",
        capture_session_path=session_path if session_path.is_file() else None,
    )
    print(f"\npackage: {result.file_count} files, {result.total_bytes / 2**30:.2f} GB → {result.root}")
    return 0


# Stable exit codes for `objects`, independent of the sidecar's own codes.
_OBJECTS_UNCONFIGURED = 2
_OBJECTS_INVALID_OUTPUT = 3
_OBJECTS_LAUNCH_FAILED = 4
_OBJECTS_SIDECAR_FAILED = 5


def cmd_object_meshes(args):
    from .object_mesh import build_object_meshes
    build_object_meshes(_run_dir(args), max_views=args.mesh_max_views,
                        long_edge=args.mesh_long_edge, poisson_depth=args.poisson_depth,
                        object_id=getattr(args, "object_id", None))
    return 0


def cmd_objects(args: argparse.Namespace) -> int:
    from .pipeline import run_lock
    root = _run_dir(args) / "object-worker"
    root.mkdir(exist_ok=True)
    with run_lock(root):
        return _cmd_objects_locked(args)


def _cmd_objects_locked(args: argparse.Namespace) -> int:
    """Run the external object-reconstruction sidecar over this run.

    The sidecar is a *separate* project with its own environment and model
    licences; we invoke it as a subprocess and never import it, so its
    dependencies stay out of this MIT tree. It reads the run's registered
    images, COLMAP poses and scene splat, and writes per-object assets plus an
    ``objects.json`` (schema ``vitrine/object/1``) into ``<run-dir>/objects/``,
    which ``package`` then folds into the preservation manifest.

    The sidecar is given as an explicit executable plus argument list (no shell
    parsing), so paths with spaces or backslashes work identically on Windows.
    """
    import subprocess
    import tempfile

    from . import objects as objects_mod
    from .publication import publish_directory
    from .construction import atomic_json

    run_dir = _run_dir(args)
    sidecar = args.sidecar or os.environ.get("VITRINE_OBJECT_SIDECAR")
    if not sidecar:
        print(
            "no object sidecar configured. Point --sidecar at its executable, or\n"
            "set VITRINE_OBJECT_SIDECAR. Extra arguments (e.g. -m sidecar) go in\n"
            "repeated --sidecar-arg. The sidecar is a separate install — see its README.",
            file=sys.stderr,
        )
        return _OBJECTS_UNCONFIGURED

    # Run into a fresh staging directory, validate, then publish atomically. This
    # guarantees no stale files survive from a prior run and that a failed or
    # invalid invocation never replaces good existing output.
    out_dir = run_dir / "objects"
    staging = Path(tempfile.mkdtemp(prefix=".objects.staging-", dir=run_dir))

    extra = args.sidecar_arg
    if extra is None:
        extra = json.loads(os.environ.get("VITRINE_OBJECT_SIDECAR_ARGS_JSON", "[]"))
    if not isinstance(extra, list) or not all(isinstance(arg, str) for arg in extra):
        raise ValueError("Sidecar arguments must be a JSON string array")
    command = [sidecar, *extra, "--package", str(run_dir), "--out", str(staging)]
    logger.info("objects: launching %r with %d arg(s)", sidecar, len(command) - 1)
    import time
    started = time.time()
    try:
        with (staging / "sidecar.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=getattr(args, "timeout", 1800))
        atomic_json(staging / "worker.json", {"returncode": result.returncode,
                    "elapsed_seconds": time.time() - started, "log": "sidecar.log"})
    except subprocess.TimeoutExpired as exc:
        atomic_json(staging / "failure.json", {"state": "timeout", "message": str(exc),
                    "elapsed_seconds": time.time() - started, "log": "sidecar.log"})
        logger.error("Sidecar timed out; evidence retained in %s", staging)
        return _OBJECTS_SIDECAR_FAILED
    except OSError as exc:
        logger.error("could not launch sidecar %r: %s", sidecar, exc)
        atomic_json(staging / "failure.json", {"state": "failed", "message": str(exc)})
        return _OBJECTS_LAUNCH_FAILED
    if result.returncode != 0:
        logger.error("sidecar exited with status %d", result.returncode)
        atomic_json(staging / "failure.json", {"state": "failed", "returncode": result.returncode})
        return _OBJECTS_SIDECAR_FAILED

    # Exit zero is not enough: require a valid contract document before trusting
    # the output, so a silent or partial run is reported as a failure here.
    try:
        records = objects_mod.load_validated_objects(staging)
    except objects_mod.ObjectManifestError as exc:
        logger.error("sidecar finished but its output is invalid: %s", exc)
        atomic_json(staging / "failure.json", {"state": "invalid", "message": str(exc)})
        return _OBJECTS_INVALID_OUTPUT

    publish_directory(staging, out_dir)

    labels = ", ".join(rec["label"] for rec in records) or "none"
    print(f"\nobjects: {len(records)} recovered — {labels}")
    return 0


def cmd_capture_session(args: argparse.Namespace) -> int:
    """Validate a Vitrine Capture session folder or zip without ingesting it."""
    from .capture_session import CaptureSessionError, open_session, validate_session

    try:
        with open_session(Path(args.path)) as root:
            session = validate_session(root)
    except CaptureSessionError as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1
    print(f"valid  {session.session_id}  {session.title}")
    print(f"  {len(session.stills)} stills  {len(session.videos)} video")
    for warning in session.warnings:
        print(f"  warning: {warning}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .package import verify_package

    ok, problems = verify_package(Path(args.package))
    if ok:
        print("All files verify against the manifest.")
        return 0
    print(f"{len(problems)} problem(s):")
    for problem in problems[:50]:
        print(f"  {problem}")
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    """Run independently recoverable stages with a mandatory preflight."""
    from .pipeline import restore_args, run_pipeline
    args = restore_args(args)
    if args.quality == "demo":
        # Keep expensive geometry observation opt-in until a full rehearsal
        # measures its cost. Heartbeats, counters and evaluation images remain.
        os.environ.setdefault("VITRINE_LIVE_PREVIEWS", "0")
    if getattr(args, "session", None) and list(args.originals) == ["source"]:
        # Default --originals is the repo source/ tree. A session is staged into
        # the run; package that, not whatever happens to sit at ./source.
        args.originals = [str(Path(args.run_dir) / "source")]
    if getattr(args, "session", None) and not getattr(args, "resume", False):
        from .capture_session import import_session
        import_session(Path(args.session), Path(args.run_dir))
        args.source = str(Path(args.run_dir) / "source")
        args.session = None
    if getattr(args, "capture_type", None):
        from .construction import atomic_json
        record_path = Path(args.run_dir) / "capture.json"
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
        record["capture_type"] = args.capture_type
        atomic_json(record_path, record)
    if list(args.originals) == ["source"] and args.source != "source":
        args.originals = [args.source]
    stages = [("ingest", cmd_ingest), ("sfm", cmd_sfm), ("train", cmd_train),
              ("export", cmd_export), ("evaluate", cmd_evaluate)]
    if getattr(args, "cleanup", False):
        stages.append(("cleanup", cmd_cleanup))
    stages += [("package", cmd_package)]
    return run_pipeline(args, stages)


def cmd_profiles(args: argparse.Namespace) -> int:
    from .hardware import detect_hardware
    hardware = detect_hardware()
    print(f"{'profile':<24}{'source':>8}{'crop':>7}{'cap':>12}{'iters':>8}{'~min':>7}")
    print("-" * 66)
    for tier in profiles.TIERS:
        for quality in profiles.QUALITY_LEVELS:
            p = profiles.resolve(quality, tier)
            estimate = p.estimated_minutes(hardware=hardware)
            estimate_text = f"{estimate:.0f}" if estimate is not None else "n/a"
            print(f"{p.name:<24}{p.source_long_edge:>8}{p.crop:>7}{p.cap_max:>12,}"
                  f"{p.iterations:>8}{estimate_text:>7}")
            for warning in p.validation_warnings():
                print(f"  warning: {warning}")
    print(f"\ndetected tier on this machine: {profiles.detect_tier()}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Serve a local dashboard over the runs/ tree (inspection only)."""
    from .serve import serve

    if getattr(args, "desktop", False):
        from .desktop import run_desktop
        return run_desktop(port=args.port, only=args.only)

    only = getattr(args, "only", None) or None
    runs_root = Path(args.runs_root).expanduser() if getattr(args, "runs_root", None) else None
    serve(
        host=args.host,
        port=args.port,
        open_browser=args.open,
        runs_root=runs_root,
        only=only,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vitrine",
        description="Local, reproducible 3D Gaussian Splatting for digital preservation.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--run-dir", default="runs/default", help="working directory for this capture")
    parser.add_argument("--quality", default="demo", choices=profiles.QUALITY_LEVELS)
    parser.add_argument("--tier", default=None, choices=profiles.TIERS,
                        help="override GPU tier detection")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check this machine can run the pipeline").set_defaults(func=cmd_doctor)
    sub.add_parser("profiles", help="show the profile table").set_defaults(func=cmd_profiles)

    p_preflight = sub.add_parser("preflight", help="check dependencies and inputs before reconstruction")
    p_preflight.add_argument("--source", default=None)
    p_preflight.add_argument("--gpu", default="auto", choices=("auto", "yes", "no"))
    p_preflight.set_defaults(func=cmd_preflight)
    sub.add_parser("export", help="prepare the browser splat from the master PLY").set_defaults(func=cmd_export)
    sub.add_parser("cleanup", help="write a separate optional cleanup candidate").set_defaults(func=cmd_cleanup)
    p_mesh = sub.add_parser("mesh-images", help="optional local isolated-image provider to GLB")
    p_mesh.add_argument("--input", required=True, help="isolated-image/mask JSON manifest")
    p_mesh.add_argument("--output", required=True, help="new output directory")
    p_mesh.add_argument("--timeout", type=int, default=1800)
    p_mesh.set_defaults(func=cmd_mesh_images)

    p_ui = sub.add_parser("ui", help="local web dashboard for runs and artefacts")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--desktop", action="store_true", help="open the Windows desktop app")
    p_ui.add_argument("--open", action="store_true", help="open the browser")
    p_ui.add_argument(
        "--runs-root",
        default=None,
        help="external directory containing capture run folders (defaults to <project>/runs)",
    )
    p_ui.add_argument(
        "--only",
        action="append",
        default=None,
        metavar="RUN",
        help="show only these run folder names (repeatable); other runs stay on disk",
    )
    p_ui.set_defaults(func=cmd_ui)

    p_ingest = sub.add_parser("ingest", help="classify, extract and select source frames")
    p_ingest.add_argument("--source", default="source")
    p_ingest.add_argument(
        "--session",
        default=None,
        help="Vitrine Capture session folder or .zip; staged into <run-dir>/source before ingest",
    )
    p_ingest.add_argument("--stills-budget", type=int, default=400)
    p_ingest.add_argument("--video-budget", type=int, default=None, help="explicit legacy per-video frame budget")
    p_ingest.add_argument("--selection-preset", choices=("fast-demo", "balanced", "archive"), default="balanced")
    p_ingest.add_argument("--include", nargs="*", default=None,
                          help="only these source subfolders (e.g. stills video)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_session = sub.add_parser("capture-session", help="validate a Vitrine Capture session")
    p_session_sub = p_session.add_subparsers(dest="session_command", required=True)
    p_session_validate = p_session_sub.add_parser("validate", help="check a session folder or .zip")
    p_session_validate.add_argument("path")
    p_session_validate.set_defaults(func=cmd_capture_session)

    p_sfm = sub.add_parser("sfm", help="solve camera poses with COLMAP")
    p_sfm.add_argument("--gpu", default="auto", choices=("auto", "yes", "no"))
    p_sfm.add_argument("--matching", default="auto", choices=("auto", "exhaustive", "sequential"))
    p_sfm.set_defaults(func=cmd_sfm)

    p_train = sub.add_parser("train", help="train the Gaussian splat")
    p_train.add_argument("--iterations", type=int, default=None, help="override the profile")
    p_train.add_argument("--eval-every", type=int, default=2000)
    p_train.set_defaults(func=cmd_train)

    p_package = sub.add_parser("package", help="assemble the preservation package")
    p_package.add_argument("--originals", nargs="+", default=["source"])
    p_package.add_argument("--title", default="3D reconstruction")
    p_package.add_argument("--subject", default="Not recorded.")
    p_package.add_argument("--capture-type", choices=("scene", "object"), default=None, help="Capture subject type (default: recorded type or scene)")
    p_package.set_defaults(func=cmd_package)

    p_object_meshes = sub.add_parser("object-meshes", help="experimental separated splat to coloured mesh conversion")
    p_object_meshes.add_argument("--object-id", default=None, help="reconstruct only this exact object ID")
    p_object_meshes.add_argument("--mesh-max-views", type=int, default=120)
    p_object_meshes.add_argument("--mesh-long-edge", type=int, default=1200)
    p_object_meshes.add_argument("--poisson-depth", type=int, default=10)
    p_object_meshes.set_defaults(func=cmd_object_meshes)

    p_objects = sub.add_parser("objects", help="run the external object-reconstruction sidecar")
    p_objects.add_argument("--timeout", type=int, default=1800, help="sidecar timeout in seconds")
    p_objects.add_argument("--sidecar", default=None,
                           help="sidecar executable (else $VITRINE_OBJECT_SIDECAR)")
    p_objects.add_argument("--sidecar-arg", action="append", default=None, metavar="ARG",
                           help="extra argument passed before --package/--out (repeatable)")
    p_objects.set_defaults(func=cmd_objects)

    p_evaluate = sub.add_parser("evaluate", help="measure a trained splat, per camera group")
    p_evaluate.add_argument("--ply", default=None, help="defaults to <run-dir>/model/scene.ply")
    p_evaluate.set_defaults(func=cmd_evaluate)

    p_verify = sub.add_parser("verify", help="re-check a package against its manifest")
    p_verify.add_argument("package")
    p_verify.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", help="ingest + sfm + train + evaluate + export + package")
    p_run.add_argument("--source", default="source")
    p_run.add_argument(
        "--session",
        default=None,
        help="Vitrine Capture session folder or .zip; staged into <run-dir>/source before ingest",
    )
    p_run.add_argument("--stills-budget", type=int, default=400)
    p_run.add_argument("--video-budget", type=int, default=None, help="explicit legacy per-video frame budget")
    p_run.add_argument("--selection-preset", choices=("fast-demo", "balanced", "archive"), default="fast-demo")
    p_run.add_argument("--resume", action="store_true", help="retain verified completed stages using the saved recipe")
    p_run.add_argument("--cleanup", action="store_true", help="save a separate cleanup candidate; preserve the master")
    p_run.add_argument("--include", nargs="*", default=None)
    p_run.add_argument("--gpu", default="auto", choices=("auto", "yes", "no"))
    p_run.add_argument("--matching", default="auto", choices=("auto", "exhaustive", "sequential"), help="camera matching strategy; exhaustive compares every image pair")
    p_run.add_argument("--iterations", type=int, default=None)
    p_run.add_argument("--eval-every", type=int, default=2000)
    p_run.add_argument("--originals", nargs="+", default=["source"])
    p_run.add_argument("--title", default="3D reconstruction")
    p_run.add_argument("--subject", default="Not recorded.")
    p_run.add_argument("--capture-type", choices=("scene", "object"), default=None, help="Capture subject type (default: recorded type or scene)")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Windows' console codepage (cp1252 etc.) can't encode the arrows and
    # other Unicode this CLI prints — UTF-8 everywhere else, crash or silent
    # mojibake here. Reconfigure regardless of platform; a no-op where stdout
    # is already UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    from .windows_tools import configure as configure_windows_tools
    configure_windows_tools()
    parser = build_parser()
    args = parser.parse_args(argv)
    log_file = None
    if args.command in {"ingest", "sfm", "train", "package", "run", "objects", "capture-session"}:
        log_file = Path(args.run_dir) / "logs" / "vitrine.log"
    _setup_logging(args.verbose, log_file)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - top-level handler
        logger.error("%s: %s", type(exc).__name__, exc)
        if args.verbose:
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
