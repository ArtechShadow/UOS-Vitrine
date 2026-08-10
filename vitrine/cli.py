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
    print(f"  profile        : {profile.name} (~{profile.estimated_minutes():.0f} min for training)")

    import shutil as _shutil

    for tool in ("docker", "ffmpeg"):
        found = _shutil.which(tool)
        print(f"  {tool:<15}: {found or 'NOT FOUND'}")
        if not found:
            ok = False

    if _shutil.which("docker"):
        from .sfm import gpu_available

        print(f"  docker GPU     : {'yes' if gpu_available() else 'no — COLMAP will use CPU (much slower)'}")

    print("\n" + ("All good." if ok else "Problems found — see the notes above."))
    return 0 if ok else 1


# --- stages -----------------------------------------------------------------

def cmd_ingest(args: argparse.Namespace) -> int:
    from .ingest import ingest

    run_dir = _run_dir(args)
    profile = profiles.resolve(args.quality, args.tier)
    report = ingest(
        Path(args.source),
        run_dir / "ingest",
        long_edge=profile.colmap_long_edge,
        stills_budget=args.stills_budget,
        video_budget=args.video_budget,
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
    )
    print(f"\n{result.registered_images} images registered · "
          f"{result.cameras} camera model(s) · {result.points:,} points")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from .colmap_io import read_model
    from .train import train

    run_dir = _run_dir(args)
    profile = profiles.resolve(args.quality, args.tier)
    if args.iterations:
        from dataclasses import replace

        profile = replace(profile, iterations=args.iterations)

    model = read_model(run_dir / "sfm" / "sparse_text")
    report = train(
        model,
        run_dir / "ingest" / "images",
        run_dir / "model",
        profile,
        eval_every=args.eval_every,
    )
    print(f"\nPSNR {report.final_psnr} dB · SSIM {report.final_ssim} · "
          f"{report.n_gaussians:,} Gaussians · {report.minutes} min")
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

    ply = Path(args.ply) if args.ply else run_dir / "model" / "scene.ply"
    report = evaluate_ply(ply, views)
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

    result = build_package(
        run_dir / "archive",
        originals=[Path(p) for p in args.originals],
        sfm_dir=run_dir / "sfm" / "sparse_text",
        model_ply=run_dir / "model" / "scene.ply",
        database=run_dir / "sfm" / "database.db",
        title=args.title,
        subject=args.subject,
        train_report=train_report,
        ingest_report=load(run_dir / "ingest" / "ingest.json"),
        sfm_report=load(run_dir / "sfm" / "sfm.json"),
        profile=profiles.describe(profile),
    )
    print(f"\npackage: {result.file_count} files, {result.total_bytes / 2**30:.2f} GB → {result.root}")
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
    """Ingest → SfM → train → package, in one go."""
    for stage in (cmd_ingest, cmd_sfm, cmd_train, cmd_package):
        code = stage(args)
        if code != 0:
            return code
    return 0


def cmd_profiles(args: argparse.Namespace) -> int:
    print(f"{'profile':<24}{'source':>8}{'crop':>7}{'cap':>12}{'iters':>8}{'~min':>7}")
    print("-" * 66)
    for tier in profiles.TIERS:
        for quality in profiles.QUALITY_LEVELS:
            p = profiles.resolve(quality, tier)
            print(f"{p.name:<24}{p.source_long_edge:>8}{p.crop:>7}{p.cap_max:>12,}"
                  f"{p.iterations:>8}{p.estimated_minutes():>7.0f}")
    print(f"\ndetected tier on this machine: {profiles.detect_tier()}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Serve a local dashboard over the runs/ tree (inspection only)."""
    from .serve import serve

    only = getattr(args, "only", None) or None
    serve(
        host=args.host,
        port=args.port,
        open_browser=args.open,
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
    parser.add_argument("--quality", default="archive", choices=profiles.QUALITY_LEVELS)
    parser.add_argument("--tier", default=None, choices=profiles.TIERS,
                        help="override GPU tier detection")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check this machine can run the pipeline").set_defaults(func=cmd_doctor)
    sub.add_parser("profiles", help="show the profile table").set_defaults(func=cmd_profiles)

    p_ui = sub.add_parser("ui", help="local web dashboard for runs and artefacts")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8765)
    p_ui.add_argument("--open", action="store_true", help="open the browser")
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
    p_ingest.add_argument("--stills-budget", type=int, default=400)
    p_ingest.add_argument("--video-budget", type=int, default=200)
    p_ingest.add_argument("--include", nargs="*", default=None,
                          help="only these source subfolders (e.g. stills video)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_sfm = sub.add_parser("sfm", help="solve camera poses with COLMAP")
    p_sfm.add_argument("--gpu", default="auto", choices=("auto", "yes", "no"))
    p_sfm.set_defaults(func=cmd_sfm)

    p_train = sub.add_parser("train", help="train the Gaussian splat")
    p_train.add_argument("--iterations", type=int, default=None, help="override the profile")
    p_train.add_argument("--eval-every", type=int, default=2000)
    p_train.set_defaults(func=cmd_train)

    p_package = sub.add_parser("package", help="assemble the preservation package")
    p_package.add_argument("--originals", nargs="+", default=["source"])
    p_package.add_argument("--title", default="3D reconstruction")
    p_package.add_argument("--subject", default="Not recorded.")
    p_package.set_defaults(func=cmd_package)

    p_evaluate = sub.add_parser("evaluate", help="measure a trained splat, per camera group")
    p_evaluate.add_argument("--ply", default=None, help="defaults to <run-dir>/model/scene.ply")
    p_evaluate.set_defaults(func=cmd_evaluate)

    p_verify = sub.add_parser("verify", help="re-check a package against its manifest")
    p_verify.add_argument("package")
    p_verify.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", help="ingest + sfm + train + package")
    p_run.add_argument("--source", default="source")
    p_run.add_argument("--stills-budget", type=int, default=400)
    p_run.add_argument("--video-budget", type=int, default=200)
    p_run.add_argument("--include", nargs="*", default=None)
    p_run.add_argument("--gpu", default="auto", choices=("auto", "yes", "no"))
    p_run.add_argument("--iterations", type=int, default=None)
    p_run.add_argument("--eval-every", type=int, default=2000)
    p_run.add_argument("--originals", nargs="+", default=["source"])
    p_run.add_argument("--title", default="3D reconstruction")
    p_run.add_argument("--subject", default="Not recorded.")
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

    parser = build_parser()
    args = parser.parse_args(argv)
    log_file = None
    if args.command in {"ingest", "sfm", "train", "package", "run"}:
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
