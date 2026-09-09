#!/usr/bin/env python3
"""Generate a machine-readable readiness record for an A6000 rehearsal.

This script is intentionally safe to run on a fresh checkout.  It only probes
installed executables and local files; it never downloads models, contacts a
remote provider, starts a reconstruction, or changes an existing run.  A
report is a statement of observed capability and evidence, not visual approval.

Examples (PowerShell):

    .venv\\Scripts\\python.exe scripts\\rehearsal_readiness.py `
      --project-root . --run-dir runs\\my-capture --source D:\\capture `
      --offline --output runs\\my-capture\\readiness.json

    # Explicitly record a human review after the visual checklist has passed:
    .venv\\Scripts\\python.exe scripts\\rehearsal_readiness.py `
      --run-dir runs\\my-capture --approve --reviewer "Name" `
      --notes "Reviewed captured viewpoints, masks and reopened GLB."
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA = "vitrine/rehearsal-readiness/1"
DEFAULT_REPORT = "runs/a6000-readiness.json"
HARDWARE_TARGETS = {
    "low": "NVIDIA RTX 3060 Laptop GPU",
    "medium": "NVIDIA RTX A6000",
    "high": "NVIDIA RTX 5090",
}
ARTIFACT_DIRS = ("model", "objects", "object-meshes")
ARCHIVE_ARTIFACTS = ("manifest.json", "README.md")
GPU_CHECK_NAMES = {
    "GPU",
    "GPU driver",
    "GPU compute capability",
    "PyTorch CUDA",
    "Torch CUDA execution",
    "gsplat CUDA extension",
    "COLMAP GPU",
}
QUALITY_CHECK_NAMES = {
    "Input media",
    "Input readability",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(
    name: str,
    status: str,
    detail: str,
    *,
    fix: str = "",
    required: bool = True,
    measured: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the common check shape used in every report category."""

    allowed = {"pass", "fail", "warning", "blocked", "not_run", "pending", "not_configured"}
    if status not in allowed:
        raise ValueError(f"unsupported readiness status: {status}")
    value: dict[str, Any] = {
        "name": name,
        "status": status,
        "detail": str(detail),
        "required": bool(required),
    }
    if fix:
        value["fix"] = fix
    if measured:
        value["measured"] = dict(measured)
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> str:
    path = Path(path).resolve()
    root = Path(root).resolve()
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        # The source capture may deliberately live on another drive.  Keep a
        # portable label without placing a private absolute path in the report.
        return f"<external>/{path.name}"


def _artifact_paths(run_dir: Path) -> list[Path]:
    """Return published output files whose change affects review evidence.

    Originals in ``archive/originals`` are intentionally excluded: the archive
    manifest already records their checksums, and hashing a multi-gigabyte raw
    capture on every dashboard check would make the control-plane probe
    unnecessarily expensive.  Model, sidecar, mesh and archive manifest files
    are included.  Incomplete ``.working-*`` generations and live progress
    files are never treated as accepted evidence.
    """

    result: list[Path] = []
    for dirname in ARTIFACT_DIRS:
        directory = run_dir / dirname
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if any(part.startswith(".working-") for part in path.parts):
                continue
            if path.name in {"progress.json", "readiness.json", "preflight.json"}:
                continue
            result.append(path)

    archive = run_dir / "archive"
    if archive.is_dir():
        for name in ARCHIVE_ARTIFACTS:
            path = archive / name
            if path.is_file():
                result.append(path)
        derivatives = archive / "derivatives"
        if derivatives.is_dir():
            result.extend(
                path
                for path in sorted(derivatives.rglob("*"))
                if path.is_file() and not any(part.startswith(".working-") for part in path.parts)
            )
    return sorted(set(result))


def collect_artifact_hashes(run_dir: Path | None) -> dict[str, dict[str, Any]]:
    """Hash accepted output artefacts using paths relative to ``run_dir``."""

    if run_dir is None or not Path(run_dir).is_dir():
        return {}
    root = Path(run_dir).resolve()
    result: dict[str, dict[str, Any]] = {}
    for path in _artifact_paths(root):
        try:
            result[_safe_relative(path, root)] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        except (OSError, ValueError) as exc:
            # A disappearing file is evidence instability, not a hash to trust.
            result[_safe_relative(path, root)] = {"error": str(exc)}
    return result


def _command(command: str | None) -> str | None:
    if not command:
        return None
    path = Path(command).expanduser()
    return str(path) if path.is_file() else shutil.which(command)


def _run(command: Iterable[str], timeout: float = 30) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    detail = (result.stdout or result.stderr or "").strip()
    return result.returncode == 0, detail[-1200:]


def _public_check(check: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """Normalize a preflight check and remove absolute local paths.

    ``vitrine.preflight`` uses ``ok``/``error`` for its internal status so it
    can be printed by the legacy CLI.  The readiness contract deliberately
    uses ``pass``/``fail`` and carries the optionality bit into ``required``;
    otherwise an error from a required runtime check could be silently ignored
    by the acceptance gate.
    """

    value = dict(check)
    raw_status = str(value.get("status", "fail")).lower()
    status_map = {
        "ok": "pass",
        "error": "fail",
        "warning": "warning",
        "not_run": "not_run",
        "pass": "pass",
        "fail": "fail",
        "blocked": "blocked",
        "pending": "pending",
        "not_configured": "not_configured",
    }
    value["status"] = status_map.get(raw_status, "fail")
    value["required"] = bool(value.get("required", not bool(value.get("optional", False))))
    for key in ("detail", "fix"):
        text = str(value.get(key, ""))
        text = text.replace(str(root.resolve()), "<project>")
        # Windows drive prefixes and Unix home paths are not useful in a
        # public report.  Keep command names and actionable prose intact.
        text = re.sub(r"[A-Za-z]:\\[^;\n,]+", lambda match: Path(match.group(0)).name, text)
        text = re.sub(r"(?<![\w/])/(?:[^\s;,]+/)+[^\s;,]+", lambda match: Path(match.group(0)).name, text)
        value[key] = text
    return value


def _preflight_checks(
    root: Path,
    run_dir: Path,
    source: Path | None,
    *,
    port: int | None,
    sidecar: str | None,
    sidecar_weights: Iterable[str] | None,
    mesh_command: Iterable[str] | None,
    mesh_weights: Iterable[str] | None,
    gpu_probe: bool,
    gsplat_probe: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the package preflight once and partition it for the readiness report."""

    try:
        # Keep this import inside the function so ``--help`` and report parsing
        # remain usable from a checkout with only the Python standard library.
        from vitrine.preflight import check_preflight

        report = check_preflight(
            source,
            run_dir,
            require_gpu=True,
            check_sfm=True,
            check_training=True,
            check_media=source is not None,
            sfm_gpu=True,
            port=port,
            sidecar=sidecar,
            sidecar_weights=sidecar_weights,
            mesh_command=mesh_command,
            mesh_weights=mesh_weights,
            probe_gpu=gpu_probe,
            probe_gsplat=gsplat_probe,
        )
    except Exception as exc:  # noqa: BLE001 - readiness must explain failures
        return [
            _check(
                "Preflight execution",
                "fail",
                f"preflight could not complete: {type(exc).__name__}: {exc}",
                fix="Run with the supported project Python and inspect the traceback in the console.",
            )
        ], {}

    checks = [_public_check(check, root) for check in report.get("checks", [])]
    return checks, dict(report.get("hardware", {}))


def _software_checks(root: Path, checks: Iterable[Mapping[str, Any]], python_executable: Path | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    version = sys.version_info
    selected = str(python_executable) if python_executable else sys.executable
    if (version.major, version.minor) == (3, 11):
        result.append(_check("Python baseline", "pass", platform.python_version(), measured={"executable": "selected"}))
    elif version.major == 3 and version.minor >= 11:
        result.append(_check("Python baseline", "warning", f"{platform.python_version()} detected; Python 3.11 is the supported baseline",
                             fix="Use a target-side Python 3.11 virtual environment for the A6000 rehearsal.", required=True))
    else:
        result.append(_check("Python baseline", "fail", f"{platform.python_version()} is below Python 3.11",
                             fix="Create an isolated environment with Python 3.11; do not change the existing environment in place."))
    result.append(_check("Selected interpreter", "pass" if Path(selected).exists() else "warning",
                         selected if Path(selected).exists() else f"not found: {selected}",
                         fix="Pass --python with the target environment's Python executable.", required=True))
    for check in checks:
        if check["name"] not in GPU_CHECK_NAMES and check["name"] not in QUALITY_CHECK_NAMES:
            result.append(dict(check))
    return result


def _gpu_checks(
    checks: Iterable[Mapping[str, Any]],
    hardware: Mapping[str, Any],
    hardware_target: str,
) -> list[dict[str, Any]]:
    result = [dict(check) for check in checks if check["name"] in GPU_CHECK_NAMES]
    if not result:
        result.append(_check("GPU probe", "not_run", "No GPU checks were returned by preflight.", required=True))
    model = hardware.get("gpu_model")
    compute = hardware.get("compute_capability")
    expected = HARDWARE_TARGETS[hardware_target]
    result.append(_check(
        "Target GPU identity",
        "pass" if model and compute else "blocked",
        f"{model or 'unknown GPU'}; compute capability {compute or 'unknown'}",
        fix=f"Confirm the actual {expected} and record its driver/compute capability before the rehearsal.",
    ))
    model_text = str(model or "").lower()
    expected_tokens = {
        "low": ("3060",),
        "medium": ("a6000",),
        "high": ("5090",),
    }[hardware_target]
    target_matches = bool(model) and any(token in model_text for token in expected_tokens)
    result.append(_check(
        "Hardware target",
        "pass" if target_matches else "fail" if model else "blocked",
        f"requested {hardware_target} ({expected}); observed {model or 'no GPU'}",
        fix=f"Run this rehearsal on the requested {expected}; a capability override is not a hardware benchmark.",
    ))
    return result


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _contained(path: Path, base: Path) -> Path | None:
    """Resolve an output path and refuse traversal/symlink escapes."""

    try:
        candidate = (base / path).resolve()
        candidate.relative_to(base.resolve())
    except (OSError, ValueError):
        return None
    return candidate


def _published_meshes(run_dir: Path) -> list[Path]:
    """Return only GLBs referenced by the latest complete mesh generation.

    Failed attempts and older immutable generations remain useful evidence, but
    they must not be counted as the current object result.  A sidecar GLB is
    included only when an object manifest references it explicitly.
    """

    paths: list[Path] = []
    object_root = run_dir / "objects"
    object_manifest = _load_json(object_root / "objects.json")
    if object_manifest:
        for record in object_manifest.get("objects", []):
            if not isinstance(record, dict):
                continue
            raw = record.get("mesh_path")
            if isinstance(raw, str) and raw.lower().endswith(".glb"):
                path = _contained(Path(raw), object_root)
                if path and path.is_file() and not path.is_symlink():
                    paths.append(path)

    mesh_root = run_dir / "object-meshes"
    latest = _load_json(mesh_root / "latest.json")
    if not latest or latest.get("schema") != "vitrine/object-mesh/1":
        return paths
    generation = latest.get("generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{32}", generation):
        return paths
    folder = mesh_root / "published" / generation
    manifest = _load_json(folder / "manifest.json")
    if manifest != latest:
        return paths
    for record in latest.get("objects", []):
        if not isinstance(record, dict):
            continue
        raw = record.get("glb_file")
        if not isinstance(raw, str) or not re.fullmatch(r"object-[0-9]{4,}\.glb", raw):
            continue
        path = _contained(Path(raw), folder)
        expected_hash = record.get("glb_sha256")
        if (path and path.is_file() and not path.is_symlink()
                and isinstance(expected_hash, str) and sha256_file(path) == expected_hash):
            paths.append(path)
    return sorted(set(paths))


def _quality_checks(
    run_dir: Path,
    root: Path,
    source: Path | None,
    *,
    sidecar: str | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    run_exists = run_dir.is_dir()
    if not run_exists:
        return [
            _check("Run directory", "not_run", f"no run at {_safe_relative(run_dir, root)}",
                   fix="Run a real capture into a new run directory, then regenerate this report.", required=True),
            _check("Object sidecar contract", "not_run",
                   "object isolation cannot start before a real scene run exists",
                   fix="Complete ingest, COLMAP and Gaussian scene training, then run the configured local sidecar.",
                   required=True),
            _check("Object mesh structure", "not_run",
                   "object surface reconstruction has not run",
                   fix="After validated object separation, publish a supported object GLB and inspect it visually.",
                   required=True),
        ]

    pipeline = _load_json(run_dir / "pipeline.json")
    if pipeline and pipeline.get("state") == "complete":
        result.append(_check("Pipeline state", "pass", "recoverable pipeline reports complete"))
    elif pipeline:
        result.append(_check("Pipeline state", "warning", str(pipeline.get("state", "unknown")),
                             fix="Resolve or resume the failed stage; process disappearance must remain failed/unknown."))
    else:
        result.append(_check("Pipeline state", "not_run", "no pipeline.json; stages may have been run individually", required=False))

    ingest = _load_json(run_dir / "ingest" / "ingest.json")
    image_count = sum(1 for path in (run_dir / "ingest" / "images").rglob("*")
                      if path.is_file()) if (run_dir / "ingest" / "images").is_dir() else 0
    result.append(_check("Ingest evidence", "pass" if ingest and image_count else "not_run",
                         f"{image_count} prepared image file(s)" if ingest else "ingest report unavailable",
                         fix="Run ingest from the real stills/video source and check sharpness/coverage diagnostics.",
                         required=True if source is not None else False,
                         measured={"prepared_files": image_count} if ingest else None))

    sfm = _load_json(run_dir / "sfm" / "sfm.json")
    sparse = run_dir / "sfm" / "sparse_text"
    sparse_ok = all((sparse / name).is_file() for name in ("cameras.txt", "images.txt", "points3D.txt"))
    result.append(_check("COLMAP evidence", "pass" if sfm and sparse_ok else "not_run",
                         "camera model and sparse text model present" if sfm and sparse_ok else "COLMAP outputs unavailable",
                         fix="Run the configured Docker COLMAP backend; never substitute fake poses.", required=True if source is not None else False))

    scene = run_dir / "model" / "scene.ply"
    scene_receipt: dict[str, Any] | None = None
    scene_error = "model/scene.ply unavailable"
    if scene.is_file():
        try:
            from vitrine.ply import verify_splat_ply

            scene_receipt = verify_splat_ply(scene, require_nonempty=True)
            scene_error = f"{scene_receipt['count']:,} finite Gaussian records"
        except Exception as exc:  # noqa: BLE001 - readiness boundary
            scene_error = f"saved scene failed structural verification: {exc}"
    scene_ok = scene_receipt is not None
    result.append(_check("Saved Gaussian scene", "pass" if scene_ok else "fail" if scene.is_file() else "not_run",
                         scene_error,
                         fix="Complete training and verify the full-SH scene before optional postprocessing.",
                         required=True if source is not None else False,
                         measured={"count": scene_receipt["count"], "sh_degree": scene_receipt["sh_degree"]}
                         if scene_receipt else None))

    splat = run_dir / "model" / "scene.splat"
    splat_ok = splat.is_file() and splat.stat().st_size > 0 and splat.stat().st_size % 32 == 0
    result.append(_check("Viewer export", "pass" if splat_ok else "not_run",
                         f"{splat.stat().st_size:,} bytes with complete records" if splat_ok else "model/scene.splat unavailable or incomplete",
                         fix="Run the explicit export stage and reopen the result in the local viewer.", required=True if source is not None else False))

    # The supported object route is the in-repo depth/fusion path backed by
    # MeshLab's pymeshlab Poisson implementation.  Keep this import lazy and
    # separate from the optional image/mask provider: a configured external
    # adapter cannot substitute for the required observed object mesher.
    if run_exists or source is not None:
        try:
            import importlib.util

            mesher_spec = importlib.util.find_spec("pymeshlab")
            mesher_ok = mesher_spec is not None
            mesher_detail = "pymeshlab module is available" if mesher_ok else "pymeshlab is not installed"
        except (ImportError, ValueError, OSError) as exc:
            mesher_ok = False
            mesher_detail = f"pymeshlab probe failed: {exc}"
        result.append(_check(
            "Object mesher (pymeshlab)",
            "pass" if mesher_ok else "not_run",
            mesher_detail,
            fix="Install the pinned requirements-mesh.txt in the isolated environment before object meshing.",
            required=True,
        ))

    evaluation = _load_json(run_dir / "model" / "evaluation.json")
    metrics = {}
    if evaluation:
        for key in ("psnr", "ssim", "mean_psnr", "mean_ssim"):
            if key in evaluation:
                metrics[key] = evaluation[key]
    result.append(_check("Held-out evaluation", "pass" if evaluation else "not_run",
                         "saved evaluation report present" if evaluation else "no canonical evaluation report",
                         fix="Run the independent evaluate stage; do not compare mismatched captures or splits.",
                         required=False, measured=metrics or None))

    archive = run_dir / "archive"
    manifest = archive / "manifest.json"
    if manifest.is_file():
        try:
            from vitrine.package import verify_package

            verified, problems = verify_package(archive)
            result.append(_check("Preservation archive", "pass" if verified else "fail",
                                 "manifest checksums verify" if verified else "; ".join(problems[:5]),
                                 fix="Rebuild or repair the archive while retaining the previous known-good package."))
        except Exception as exc:  # noqa: BLE001 - report dependency/runtime boundary
            result.append(_check("Preservation archive", "fail", f"verification unavailable: {exc}",
                                 fix="Run vitrine verify with the project environment."))
    else:
        result.append(_check("Preservation archive", "not_run", "archive/manifest.json unavailable",
                             fix="Package the scene only after the saved PLY and optional derivatives are verified.",
                             required=True if source is not None else False))

    objects = run_dir / "objects" / "objects.json"
    configured_sidecar = sidecar or os.environ.get("VITRINE_OBJECT_SIDECAR", "").strip()
    if source is not None:
        result.append(_check(
            "Object sidecar configured",
            "pass" if configured_sidecar else "not_run",
            "separate local sidecar executable is configured" if configured_sidecar else "no local object sidecar configured",
            fix="Configure the separately installed local SAM2.1 sidecar before the object-isolation step.",
            required=True,
        ))
    if objects.is_file():
        try:
            from vitrine.objects import load_validated_objects

            records = load_validated_objects(objects.parent)
            mesh_records = [record for record in records if record.get("mesh_path")]
            result.append(_check("Object sidecar contract", "pending",
                                 f"{len(records)} validated object record(s); identity and completeness require visual review",
                                 fix="Inspect each selected object from captured viewpoints before acceptance.", required=True,
                                 measured={"records": len(records)}))
            if not mesh_records:
                result.append(_check("Object mesh evidence", "not_run",
                                     "validated sidecar records contain no reconstructed mesh asset",
                                     fix="Run a supported object-surface reconstruction and publish a referenced GLB.",
                                     required=True))
        except Exception as exc:  # noqa: BLE001
            result.append(_check("Object sidecar contract", "fail", str(exc),
                                 fix="Repair the sidecar output contract or retain the previous accepted generation.", required=True))
    else:
        result.append(_check("Object sidecar contract", "not_run", "no validated objects.json; object isolation evidence is absent",
                             fix="Configure the separately installed sidecar and run it only after a real scene exists.", required=True))

    mesh_candidates = _published_meshes(run_dir)
    if mesh_candidates:
        try:
            from vitrine.engines import validate_glb

            for path in mesh_candidates:
                validate_glb(path)
            result.append(_check("Object mesh structure", "pending",
                                 f"{len(mesh_candidates)} GLB file(s) structurally valid; visual shape acceptance pending",
                                 fix="Inspect masks, viewpoints, scale, outliers and uncovered surfaces before export acceptance.",
                                 required=True, measured={"glb_files": len(mesh_candidates)}))
        except Exception as exc:  # noqa: BLE001
            result.append(_check("Object mesh structure", "fail", str(exc),
                                 fix="Retain failed evidence and regenerate into a new immutable generation.", required=True))
    else:
        result.append(_check("Object mesh structure", "not_run", "no referenced GLB in the latest published object generation",
                             fix="A configured supported local mesh provider is required for object-surface reconstruction.", required=True))
    return result


def _offline_checks(
    root: Path,
    run_dir: Path,
    *,
    offline: bool,
    sidecar_root: Path | None,
    mesh_weights: Iterable[str] | None,
) -> list[dict[str, Any]]:
    if not offline:
        return [
            _check("Offline rehearsal mode", "not_run", "offline mode was not requested; no network claim made",
                   fix="Repeat with --offline after online preparation and model warm-up.", required=False)
        ]
    result: list[dict[str, Any]] = [
        _check("Offline rehearsal mode", "pending",
               "core offline flags are set; external sidecar/provider network behavior requires runtime verification",
               fix="Inspect the process logs and local network monitor during an offline rehearsal before claiming offline operation."),
        _check("Local dashboard assets", "pass" if (root / "vitrine" / "ui" / "viewer.html").is_file() else "fail",
               "bundled viewer entry point present" if (root / "vitrine" / "ui" / "viewer.html").is_file() else "viewer.html missing",
               fix="Restore the tracked local UI assets before disconnecting the workstation."),
    ]
    if sidecar_root is None:
        result.append(_check("Sidecar model cache", "not_run", "optional sidecar is not configured",
                             fix="If object isolation is required, verify the separate sidecar environment and weights online, then rerun offline.",
                             required=False))
    elif sidecar_root.is_dir():
        result.append(_check("Sidecar model cache", "pass", "configured sidecar root is present",
                             measured={"path": _safe_relative(sidecar_root, root)}))
    else:
        result.append(_check("Sidecar model cache", "blocked", "configured sidecar root is missing",
                             fix="Place the separately installed sidecar and its licensed local model cache on the Lab workstation."))
    values = list(mesh_weights or [])
    if not values:
        result.append(_check("Optional mesh model cache", "not_run", "no optional mesh weights declared",
                             required=False))
    else:
        missing = [str(path) for path in values if not Path(path).is_file() or Path(path).stat().st_size == 0]
        result.append(_check("Optional mesh model cache", "pass" if not missing else "blocked",
                             f"{len(values)} local weight file(s) present" if not missing else "missing/empty: " + ", ".join(missing[:5]),
                             fix="Prepare and verify optional adapter weights while online; no download occurs offline.", required=False))
    if run_dir.is_dir():
        result.append(_check("Prepared run is local", "pass", _safe_relative(run_dir, root), required=False))
    else:
        result.append(_check("Prepared run is local", "not_run", "no run directory supplied", required=False))
    return result


def _approval(previous: Mapping[str, Any] | None, hashes: Mapping[str, Any], *, approve: bool,
              reviewer: str | None, notes: str | None) -> dict[str, Any]:
    previous_record = dict((previous or {}).get("human_visual_approval") or {})
    current = {
        "status": "pending",
        "approved": False,
        "reviewer": None,
        "notes": "Human visual/demo approval is deliberately pending.",
        "artifact_hashes": dict(hashes),
    }
    if previous_record.get("approved"):
        if previous_record.get("artifact_hashes") == dict(hashes):
            current.update(previous_record)
            current["status"] = "approved"
            current["approval_hashes_match"] = True
        else:
            current["status"] = "pending"
            current["notes"] = "Previous approval invalidated because one or more artefact hashes changed."
            current["approval_invalidated"] = True
    if approve:
        if not reviewer or not reviewer.strip() or not notes or not notes.strip():
            raise ValueError("--approve requires non-empty --reviewer and --notes")
        if not hashes:
            raise ValueError("cannot record visual approval without at least one hashed output artefact")
        current = {
            "status": "approved",
            "approved": True,
            "reviewer": reviewer.strip(),
            "notes": notes.strip(),
            "recorded_at": _utc_now(),
            "artifact_hashes": dict(hashes),
            "approval_hashes_match": True,
        }
    return current


def build_readiness(
    *,
    project_root: Path,
    run_dir: Path | None = None,
    source: Path | None = None,
    output: Path | None = None,
    port: int | None = 8765,
    offline: bool = False,
    python_executable: Path | None = None,
    sidecar: str | None = None,
    sidecar_weights: Iterable[str] | None = None,
    sidecar_root: Path | None = None,
    mesh_command: Iterable[str] | None = None,
    mesh_weights: Iterable[str] | None = None,
    hardware_target: str = "medium",
    gpu_probe: bool = True,
    gsplat_probe: bool = True,
    approve: bool = False,
    reviewer: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Build and return a readiness report without writing it."""

    root = Path(project_root).expanduser().resolve()
    run = Path(run_dir).expanduser().resolve() if run_dir is not None else None
    selected_source = Path(source).expanduser().resolve() if source is not None else None
    if run is None:
        run = root / "runs" / "a6000-rehearsal"

    checks, hardware = _preflight_checks(
        root,
        run,
        selected_source,
        port=port,
        sidecar=sidecar,
        sidecar_weights=sidecar_weights,
        mesh_command=mesh_command,
        mesh_weights=mesh_weights,
        gpu_probe=gpu_probe,
        gsplat_probe=gsplat_probe,
    )
    # Keep the contract boundary here as well as in ``_preflight_checks``:
    # callers and deterministic test fixtures may supply legacy ``ok``/
    # ``error`` records directly.  Required errors must never disappear from
    # the gate simply because the producer bypassed the adapter.
    checks = [_public_check(check, root) for check in checks]
    software = _software_checks(root, checks, python_executable)
    if hardware_target not in HARDWARE_TARGETS:
        raise ValueError(f"hardware_target must be one of {tuple(HARDWARE_TARGETS)}")
    gpu = _gpu_checks(checks, hardware, hardware_target)
    quality = _quality_checks(run, root, selected_source, sidecar=sidecar)
    offline_checks = _offline_checks(
        root,
        run,
        offline=offline,
        sidecar_root=sidecar_root,
        mesh_weights=mesh_weights,
    )
    hashes = collect_artifact_hashes(run)

    previous: dict[str, Any] | None = None
    report_path = Path(output).expanduser().resolve() if output is not None else root / DEFAULT_REPORT
    if report_path.is_file():
        previous = _load_json(report_path)
    approval = _approval(previous, hashes, approve=approve, reviewer=reviewer, notes=notes)

    required_checks = [*software, *gpu, *quality, *offline_checks]
    blocking = [
        check for check in required_checks
        if check.get("required") and check.get("status") in {"fail", "blocked", "not_run"}
    ]
    structural_quality = [
        check for check in quality
        if check.get("required") and check.get("status") in {"fail", "blocked", "not_run"}
    ]
    if blocking or structural_quality:
        overall = "blocked"
    elif approval.get("approved"):
        overall = "human visual approval recorded"
    else:
        overall = "candidate for A6000 rehearsal; visual acceptance pending"

    report: dict[str, Any] = {
        "schema": SCHEMA,
        "generated_at": _utc_now(),
        "status": overall,
        "target": {
            "hardware_target": hardware_target,
            "hardware": HARDWARE_TARGETS[hardware_target],
            "platform": "Windows",
            "python_baseline": "3.11",
            "visual_approval_required": True,
        },
        "project": {
            "root": ".",
            "run_dir": _safe_relative(run, root),
            "source": _safe_relative(selected_source, root) if selected_source else None,
            "report": _safe_relative(report_path, root),
        },
        "machine": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "python_executable": "selected interpreter",
            "hardware": {
                key: value for key, value in hardware.items()
                if "path" not in key.lower() and key not in {"source_gpu", "source_ram", "config_path"}
            },
        },
        "software_checks": software,
        "real_gpu_execution": gpu,
        "reconstruction_quality": quality,
        "offline_operation": offline_checks,
        "artifact_hashes": hashes,
        "human_visual_approval": approval,
        "blocking_checks": blocking,
    }
    return report


def write_report(report: Mapping[str, Any], output: Path) -> Path:
    """Atomically publish a report, preserving a prior complete report."""

    destination = Path(output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, destination)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return destination


def _print_summary(report: Mapping[str, Any]) -> None:
    print(f"Readiness: {report['status']}")
    for category in ("software_checks", "real_gpu_execution", "reconstruction_quality", "offline_operation"):
        checks = report.get(category, [])
        failed = sum(1 for check in checks if check.get("status") in {"fail", "blocked"})
        pending = sum(1 for check in checks if check.get("status") in {"pending", "not_run", "not_configured"})
        print(f"  {category}: {len(checks)} checks, {failed} failure(s), {pending} pending/not-run")
    print(f"  artefacts hashed: {len(report.get('artifact_hashes', {}))}")
    approval = report.get("human_visual_approval", {})
    print(f"  human visual approval: {approval.get('status', 'pending')}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--source", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--port", type=int, default=8765, help="dashboard port to probe; use 0 to skip")
    parser.add_argument("--offline", action="store_true", help="record checks for a network-disconnected rehearsal")
    parser.add_argument("--python", dest="python_executable", type=Path, default=None)
    parser.add_argument("--sidecar", default=None)
    parser.add_argument("--sidecar-weight", action="append", default=None)
    parser.add_argument("--sidecar-root", type=Path, default=None)
    parser.add_argument("--hardware-target", choices=tuple(HARDWARE_TARGETS), default="medium",
                        help="efficiency/rehearsal target: low RTX 3060 Laptop, medium RTX A6000, high RTX 5090")
    parser.add_argument("--mesh-command", action="append", default=None,
                        help="optional mesh adapter argv; repeat for each argument")
    parser.add_argument("--mesh-weight", action="append", default=None)
    parser.add_argument("--no-gpu-probe", action="store_true")
    parser.add_argument("--no-gsplat-probe", action="store_true")
    parser.add_argument("--approve", action="store_true", help="explicitly record a human review")
    parser.add_argument("--reviewer", default=None)
    parser.add_argument("--notes", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.expanduser().resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    output = args.output or (root / DEFAULT_REPORT)
    report = build_readiness(
        project_root=root,
        run_dir=args.run_dir,
        source=args.source,
        output=output,
        port=None if args.port == 0 else args.port,
        offline=args.offline,
        python_executable=args.python_executable,
        sidecar=args.sidecar,
        sidecar_weights=args.sidecar_weight,
        sidecar_root=args.sidecar_root,
        mesh_command=args.mesh_command,
        mesh_weights=args.mesh_weight,
        hardware_target=args.hardware_target,
        gpu_probe=not args.no_gpu_probe,
        gsplat_probe=not args.no_gsplat_probe,
        approve=args.approve,
        reviewer=args.reviewer,
        notes=args.notes,
    )
    write_report(report, output)
    _print_summary(report)
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
