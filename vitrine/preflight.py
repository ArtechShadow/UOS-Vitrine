"""Check local capabilities before an expensive Vitrine stage starts.

The preflight is deliberately conservative.  It reports facts observed on the
current machine and does not install software, download weights, or substitute
a CPU/fake COLMAP result for a requested GPU run.  Optional providers are
reported as warnings so the scene pipeline can still be used when an object
sidecar or mesh adapter has not been installed.

The function is used by the recoverable pipeline as well as by the Windows
rehearsal tooling.  Keep its return value JSON serialisable: it is persisted as
``preflight.json`` and is part of the evidence used to decide whether a run can
be resumed safely.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _probe(command: Iterable[str], timeout: float = 30) -> tuple[bool, str]:
    """Run an executable without a shell and return a bounded diagnostic."""

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
        output = (result.stdout or result.stderr or "").strip()
        return result.returncode == 0, output[-1200:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def _which_or_file(command: str | None) -> str | None:
    if not command:
        return None
    path = Path(command).expanduser()
    if path.is_file():
        return str(path)
    return shutil.which(command)


def _json_string_array(value: str | None, name: str) -> tuple[list[str] | None, str | None]:
    """Parse an optional command/weights environment value safely."""

    if value is None or not value.strip():
        return None, None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        return None, f"{name} is not valid JSON: {exc.msg}"
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        return None, f"{name} must be a JSON array of non-empty strings"
    return parsed, None


def _weight_check(weights: Iterable[str] | None) -> tuple[bool, str]:
    values = list(weights or [])
    if not values:
        return False, "no local weights were declared"
    missing = []
    empty = []
    for raw in values:
        path = Path(raw).expanduser()
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False
        if not is_file:
            missing.append(raw)
        else:
            try:
                if path.stat().st_size == 0:
                    empty.append(raw)
            except OSError:
                missing.append(raw)
    if missing or empty:
        detail = []
        if missing:
            detail.append("missing: " + ", ".join(missing[:5]))
        if empty:
            detail.append("empty: " + ", ".join(empty[:5]))
        return False, "; ".join(detail)
    return True, f"{len(values)} local weight file(s) present"


def _port_available(port: int, host: str = "127.0.0.1") -> tuple[bool, str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, int(port)))
        return True, f"{host}:{port} is available"
    except (OSError, ValueError) as exc:
        return False, f"{host}:{port} is occupied or unavailable ({exc})"


def _version(module: str, distribution: str | None = None) -> str | None:
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return None
    try:
        return importlib.metadata.version(distribution or module)
    except importlib.metadata.PackageNotFoundError:
        try:
            imported = __import__(module)
            return str(getattr(imported, "__version__", "installed"))
        except (ImportError, OSError):
            return "installed"


def _module_check(module: str, distribution: str | None = None) -> tuple[bool, str]:
    version = _version(module, distribution)
    return bool(version), version or "not installed"


def check_preflight(
    source: Path | None,
    output: Path,
    *,
    require_gpu: bool = True,
    check_sfm: bool = True,
    check_training: bool = True,
    check_media: bool = True,
    sfm_gpu: bool | None = None,
    port: int | None = None,
    sidecar: str | None = None,
    sidecar_weights: Iterable[str] | None = None,
    mesh_command: Iterable[str] | None = None,
    mesh_weights: Iterable[str] | None = None,
    probe_gpu: bool = True,
    probe_gsplat: bool = True,
):
    """Return a JSON-safe report of capabilities needed by the requested stages.

    ``sidecar`` and ``mesh_command`` are optional process boundaries.  An
    absent or incomplete optional provider is a warning; a configured provider
    with missing executable/weights is still surfaced explicitly.  ``port`` is
    also a warning because an already-running dashboard may legitimately own
    it and the caller can choose another port.
    """

    from .windows_tools import configure

    configure()
    checks: list[dict[str, Any]] = []

    def add(
        name: str,
        ok: bool,
        detail: Any,
        fix: str = "",
        optional: bool = False,
        *,
        status: str | None = None,
    ) -> None:
        if status is None:
            status = "ok" if ok else "warning" if optional else "error"
        checks.append(
            {
                "name": name,
                "status": status,
                "detail": str(detail),
                "fix": "" if ok else fix,
                "optional": bool(optional),
            }
        )

    hardware: dict[str, Any] = {}
    try:
        from .hardware import detect_hardware

        detected = detect_hardware()
        if isinstance(detected, dict):
            hardware.update(detected)
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        hardware["probe_error"] = str(exc)

    torch_available = False
    torch_cuda = False
    torch_device_error: str | None = None
    if check_training or require_gpu:
        try:
            import torch

            torch_available = True
            torch_cuda = bool(torch.cuda.is_available())
            hardware.update(
                {
                    "torch_cuda_available": torch_cuda,
                    "torch_version": getattr(torch, "__version__", None),
                    "cuda_version": getattr(torch.version, "cuda", None),
                }
            )
            if torch_cuda:
                device = torch.cuda.current_device()
                prop = torch.cuda.get_device_properties(device)
                free, total = torch.cuda.mem_get_info(device)
                compute = torch.cuda.get_device_capability(device)
                hardware.update(
                    gpu_model=prop.name,
                    compute_capability=f"{compute[0]}.{compute[1]}",
                    vram_total_bytes=int(total),
                    vram_available_bytes=int(free),
                )
                add(
                    "GPU",
                    free >= 2 * 2**30,
                    f"{prop.name}; {free / 2**30:.1f} GiB free of {total / 2**30:.1f}",
                    "Close other GPU workloads; at least 2 GiB free is required to begin.",
                )
                if probe_gpu:
                    try:
                        # A tiny synchronised operation catches driver/runtime
                        # failures which ``is_available`` alone can miss.
                        probe = torch.ones(1, device=device)
                        result = (probe + 1).item()
                        torch.cuda.synchronize(device)
                        add(
                            "Torch CUDA execution",
                            result == 2,
                            f"synchronised test result {result}",
                            "Repair the NVIDIA driver/PyTorch CUDA pairing before reconstruction.",
                        )
                    except Exception as exc:  # noqa: BLE001 - diagnostic boundary
                        torch_device_error = str(exc)
                        add(
                            "Torch CUDA execution",
                            False,
                            exc,
                            "Repair the NVIDIA driver/PyTorch CUDA pairing before reconstruction.",
                        )
            add(
                "PyTorch CUDA",
                torch_cuda,
                getattr(torch.version, "cuda", None) or "CUDA unavailable",
                "Install the documented CUDA-matched PyTorch environment and NVIDIA driver; run vitrine doctor.",
                optional=not (require_gpu or check_training),
            )
        except (ImportError, OSError, RuntimeError, AssertionError) as exc:
            hardware["torch_error"] = str(exc)
            add(
                "PyTorch CUDA",
                False,
                exc,
                "Install the documented CUDA-matched PyTorch environment; do not upgrade the existing demo environment in place.",
            )

    if require_gpu:
        driver = hardware.get("driver_version")
        compute = hardware.get("compute_capability")
        add(
            "GPU driver",
            bool(driver),
            driver or "not reported by nvidia-smi",
            "Install/enable the NVIDIA driver on the Lab workstation and confirm nvidia-smi works.",
        )
        add(
            "GPU compute capability",
            bool(compute),
            compute or "not reported",
            "Confirm a CUDA-visible NVIDIA GPU; record its compute capability before warming gsplat.",
        )

    if check_training:
        packages = (
            ("torch", None),
            ("gsplat", None),
            ("numpy", None),
            ("scipy", None),
            ("PIL", "pillow"),
            ("plyfile", None),
            ("cv2", "opencv-python"),
        )
        package_errors = False
        for package, distribution in packages:
            ok, detail = _module_check(package, distribution)
            package_errors |= not ok
            add(package, ok, detail, "Install the pinned project requirements into the isolated Python environment.")

        if probe_gsplat and not package_errors and torch_cuda:
            ok, detail = _probe(
                [
                    os.sys.executable,
                    "-c",
                    "from vitrine import cuda_toolkit as c; c.configure(); "
                    "import gsplat.cuda._wrapper as w; "
                    "w._make_lazy_cuda_obj('CameraModelType.PINHOLE'); print('CUDA extension loaded')",
                ],
                timeout=240,
            )
            add(
                "gsplat CUDA extension",
                ok,
                detail,
                "Run vitrine doctor and repair the documented nvcc/MSVC or GCC toolchain before retrying.",
            )
        elif probe_gsplat:
            add(
                "gsplat CUDA extension",
                False,
                "not probed because PyTorch CUDA or a required package is unavailable",
                "Resolve the CUDA and package checks, then rerun the readiness report.",
            )

        # Object meshing is a separate CPU stage.  Probe its implementation
        # lazily so a scene-only run does not import MeshLab or its native
        # libraries, while still making the missing requirement visible to the
        # object acceptance gate.
        mesh_ok, mesh_detail = _module_check("pymeshlab", "pymeshlab")
        add(
            "pymeshlab",
            mesh_ok,
            mesh_detail,
            "Install the pinned requirements-mesh.txt in the isolated environment before object meshing.",
            optional=True,
        )
        blender = shutil.which("blender")
        add(
            "Blender",
            bool(blender),
            blender or "not found",
            "Install Blender only if the optional texture/baking handoff is required; object surface acceptance does not depend on it.",
            optional=True,
        )

    if check_sfm:
        sfm_gpu = require_gpu if sfm_gpu is None else sfm_gpu
        from .sfm import DEFAULT_IMAGE

        docker = shutil.which("docker")
        if docker:
            ok, detail = _probe([docker, "info", "--format", "{{.OSType}}"], 15)
        else:
            ok, detail = False, "docker not found"
        engine = ok and detail.strip() == "linux"
        add(
            "Docker Linux engine",
            engine,
            detail,
            "Start Docker Desktop with the WSL2/Linux container backend, then rerun preflight.",
        )
        if engine:
            ok, detail = _probe([docker, "image", "inspect", DEFAULT_IMAGE, "--format", "{{.Id}}"], 15)
            add("COLMAP image", ok, detail, f"Before the demo run: docker pull {DEFAULT_IMAGE}")
            if ok:
                gpu_args = ["--gpus", "all"] if sfm_gpu else []
                ok, detail = _probe(
                    [docker, "run", "--rm", "--pull=never", *gpu_args, DEFAULT_IMAGE, "colmap", "-h"],
                    45,
                )
                add(
                    "COLMAP runtime",
                    ok,
                    detail,
                    "Repair Docker/WSL, or explicitly choose --gpu no for slower CPU camera solving.",
                )
                if sfm_gpu and ok:
                    ok, detail = _probe(
                        [
                            docker,
                            "run",
                            "--rm",
                            "--pull=never",
                            "--gpus",
                            "all",
                            DEFAULT_IMAGE,
                            "nvidia-smi",
                            "-L",
                        ],
                        30,
                    )
                    add("COLMAP GPU", ok, detail, "Repair NVIDIA Docker/WSL GPU passthrough before reconstruction.")
                elif not sfm_gpu:
                    add(
                        "COLMAP execution",
                        False,
                        "CPU camera solving explicitly selected; reconstruction will be slower",
                        optional=True,
                    )

    output = Path(output).expanduser().resolve()
    parent = output
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        if not parent.is_dir():
            raise OSError(f"{parent} is not a directory")
        with tempfile.TemporaryFile(dir=parent) as test:
            test.write(b"vitrine-output-probe")
            test.flush()
        add("Output folder", True, "writable")
    except OSError as exc:
        add("Output folder", False, exc, "Choose a writable --run-dir on a local disk.")

    if port is not None:
        available, detail = _port_available(int(port))
        add(
            "Dashboard port",
            available,
            detail,
            "Stop the task-owned Vitrine server or choose another --port; do not start duplicate dashboards.",
            optional=True,
        )

    input_bytes = 0
    videos = False
    if source is not None and check_media:
        from .ingest import IMAGE_SUFFIXES, VIDEO_SUFFIXES

        source = Path(source).expanduser()
        files = ([source] if source.is_file() else sorted(source.rglob("*"))) if source.exists() else []
        media = [p for p in files if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES]
        add(
            "Input media",
            bool(media),
            f"{len(media)} supported files",
            "Select readable photographs or videos, or validate/import the capture session first.",
        )
        invalid: list[str] = []
        videos = any(p.suffix.lower() in VIDEO_SUFFIXES for p in media)
        if videos:
            for tool in ("ffmpeg", "ffprobe"):
                found = shutil.which(tool)
                add(tool, bool(found), found or "not found", f"Install {tool} and add it to PATH.")
        for path in media:
            try:
                input_bytes += path.stat().st_size
                if path.stat().st_size == 0:
                    raise ValueError("empty file")
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    from PIL import Image

                    with Image.open(path) as image:
                        image.verify()
                elif shutil.which("ffprobe"):
                    ok, detail = _probe(
                        [
                            "ffprobe",
                            "-v",
                            "error",
                            "-select_streams",
                            "v:0",
                            "-show_entries",
                            "stream=width,height:format=duration",
                            "-of",
                            "json",
                            str(path),
                        ],
                        20,
                    )
                    metadata = json.loads(detail) if ok else {}
                    if not metadata.get("streams") or float(metadata.get("format", {}).get("duration", 0)) <= 0:
                        raise ValueError("no video stream or finite duration")
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                invalid.append(f"{path.name}: {exc}")
        add(
            "Input readability",
            not invalid,
            "; ".join(invalid[:10]) or "media headers verified",
            "Remove unreadable files from this capture selection; preserve the originals separately.",
        )

    # Report the video toolchain even for a fresh dashboard check without a
    # source path.  It is optional for a stills-only rehearsal, but its absence
    # must be visible before a mixed still/video capture is attempted.
    if not videos:
        for tool in ("ffmpeg", "ffprobe"):
            found = shutil.which(tool)
            add(
                tool,
                bool(found),
                found or "not found",
                f"Install {tool} and add it to PATH before using video input.",
                optional=True,
            )

    try:
        free = shutil.disk_usage(parent).free
        required = max(5 * 2**30, input_bytes * 5)
        add(
            "Disk space",
            free >= required,
            f"{free / 2**30:.1f} GiB free; preliminary reserve {required / 2**30:.1f} GiB",
            "Choose a larger output disk. Extraction and archive copies need additional space.",
        )
    except OSError as exc:
        add("Disk space", False, exc, "Choose an accessible output disk.")

    sidecar = sidecar or os.environ.get("VITRINE_OBJECT_SIDECAR")
    if sidecar:
        found = _which_or_file(sidecar)
        add(
            "Object sidecar executable",
            bool(found),
            found or f"not found: {sidecar}",
            "Configure the separately licensed local sidecar executable; do not put its source or weights in this repository.",
            optional=True,
        )
        if sidecar_weights is None:
            parsed, error = _json_string_array(
                os.environ.get("VITRINE_OBJECT_SIDECAR_WEIGHTS_JSON"),
                "VITRINE_OBJECT_SIDECAR_WEIGHTS_JSON",
            )
            if error:
                add(
                    "Object sidecar weights",
                    False,
                    error,
                    "Set VITRINE_OBJECT_SIDECAR_WEIGHTS_JSON to a JSON array of local weight files.",
                    optional=True,
                )
                parsed = []
            sidecar_weights = parsed
        ok, detail = _weight_check(sidecar_weights)
        add(
            "Object sidecar weights",
            ok,
            detail,
            "Place the separately licensed sidecar weights on the workstation and configure their paths.",
            optional=True,
        )
    else:
        add(
            "Object sidecar executable",
            False,
            "not configured; scene reconstruction remains available",
            "Configure VITRINE_OBJECT_SIDECAR only when the separately installed object sidecar is ready.",
            optional=True,
        )
        add(
            "Object sidecar weights",
            False,
            "not checked because the optional sidecar is not configured",
            "Run the sidecar's own local model/weights check before object isolation.",
            optional=True,
            status="not_run",
        )

    if mesh_command is None:
        parsed, error = _json_string_array(
            os.environ.get("VITRINE_MESH_COMMAND_JSON"),
            "VITRINE_MESH_COMMAND_JSON",
        )
        if error:
            add(
                "Optional mesh adapter",
                False,
                error,
                "Set VITRINE_MESH_COMMAND_JSON to a JSON executable/argument array.",
                optional=True,
            )
            parsed = []
        mesh_command = parsed
    mesh_values = list(mesh_command or [])
    if mesh_values:
        found = _which_or_file(mesh_values[0])
        add(
            "Optional mesh adapter",
            bool(found),
            found or f"executable not found: {mesh_values[0]}",
            "Install/configure the optional local adapter; no cloud or generative provider is assumed.",
            optional=True,
        )
        if mesh_weights is None:
            parsed, error = _json_string_array(
                os.environ.get("VITRINE_MESH_WEIGHTS_JSON"),
                "VITRINE_MESH_WEIGHTS_JSON",
            )
            if error:
                add(
                    "Optional mesh weights",
                    False,
                    error,
                    "Set VITRINE_MESH_WEIGHTS_JSON to a JSON array of local weight files.",
                    optional=True,
                )
                parsed = []
            mesh_weights = parsed
        ok, detail = _weight_check(mesh_weights)
        add(
            "Optional mesh weights",
            ok,
            detail,
            "Place the adapter's local weights on the workstation and configure their paths.",
            optional=True,
        )
    else:
        add(
            "Optional mesh adapter",
            False,
            "not configured; object mesh stage is unavailable",
            "Configure VITRINE_MESH_COMMAND_JSON only for a tested local adapter.",
            optional=True,
        )
        add(
            "Optional mesh weights",
            False,
            "not checked because no adapter is configured",
            "Run the adapter's own local model/weights check before attempting mesh-images.",
            optional=True,
            status="not_run",
        )

    return {
        "schema": "vitrine/preflight/2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ready": not any(check["status"] == "error" for check in checks),
        "checks": checks,
        "hardware": hardware,
        "observations": {
            "torch_available": torch_available,
            "torch_cuda_available": torch_cuda,
            "torch_device_error": torch_device_error,
            "source": str(source) if source is not None else None,
            "output": str(output),
        },
    }


def summary(report: dict[str, Any]) -> str:
    rows = ["VITRINE PREFLIGHT"]
    for check in report["checks"]:
        rows.append(f"{check['name']:<28} {check['status'].upper():<9} {check['detail']}")
        if check.get("fix"):
            rows.append("  " + check["fix"])
    rows.append("READY TO RECONSTRUCT" if report["ready"] else "NOT READY — resolve the errors above")
    return "\n".join(rows)


__all__ = ["check_preflight", "summary"]
