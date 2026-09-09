"""Fail before decoding or training when a required local capability is absent."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def _probe(command, timeout=30):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0, (result.stdout or result.stderr).strip()[-1200:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def check_preflight(source: Path | None, output: Path, *, require_gpu=True,
                    check_sfm=True, check_training=True, check_media=True, sfm_gpu=None):
    from .windows_tools import configure
    configure()
    checks = []

    def add(name, ok, detail, fix="", optional=False):
        checks.append(dict(name=name, status="ok" if ok else "warning" if optional else "error",
                           detail=str(detail), fix="" if ok else fix))

    hardware = {}
    if check_training or require_gpu:
        try:
            import torch
            available = torch.cuda.is_available()
            hardware = dict(cuda_available=available, cuda_version=torch.version.cuda)
            if available:
                device = torch.cuda.current_device()
                prop = torch.cuda.get_device_properties(device)
                free, total = torch.cuda.mem_get_info(device)
                hardware.update(gpu_model=prop.name, compute_capability=list(torch.cuda.get_device_capability(device)),
                                vram_total_bytes=total, vram_available_bytes=free)
                add("GPU", free >= 2 * 2**30, f"{prop.name}; {free/2**30:.1f} GiB free of {total/2**30:.1f}",
                    "Close other GPU workloads; at least 2 GiB free is required to begin.")
            add("PyTorch CUDA", available, torch.version.cuda or "CUDA unavailable",
                "Install the documented CUDA-matched PyTorch environment and NVIDIA driver; run vitrine doctor.",
                optional=not (require_gpu or check_training))
        except (ImportError, OSError, RuntimeError, AssertionError) as exc:
            add("PyTorch CUDA", False, exc, "Install requirements.txt with the documented CUDA toolchain.")
    try:
        from .hardware import detect_hardware
        detected = detect_hardware()
        if hasattr(detected, "to_dict"):
            detected = detected.to_dict()
        elif hasattr(detected, "__dataclass_fields__"):
            from dataclasses import asdict
            detected = asdict(detected)
        if isinstance(detected, dict):
            hardware.update(detected)
    except (ImportError, OSError, RuntimeError):
        pass

    if check_training:
        for package in ("torch", "gsplat", "numpy", "scipy", "PIL", "plyfile", "cv2"):
            try:
                available = importlib.util.find_spec(package) is not None
            except (ImportError, ValueError):
                available = False
            add(package, available, "installed" if available else "not installed",
                "Install requirements.txt using the project Python environment.")
        if not any(c["status"] == "error" for c in checks):
            ok, detail = _probe([os.sys.executable, "-c",
                "from vitrine import cuda_toolkit as c; c.configure(); "
                "import gsplat.cuda._wrapper as w; w._make_lazy_cuda_obj('CameraModelType.PINHOLE'); "
                "print('CUDA extension loaded')"], timeout=240)
            add("gsplat CUDA extension", ok, detail,
                "Run vitrine doctor and repair the documented nvcc/MSVC or GCC toolchain before retrying.")

    if check_sfm:
        sfm_gpu = require_gpu if sfm_gpu is None else sfm_gpu
        from .sfm import DEFAULT_IMAGE
        docker = shutil.which("docker")
        ok, detail = _probe([docker, "info", "--format", "{{.OSType}}"], 15) if docker else (False, "docker not found")
        engine = ok and detail == "linux"
        add("Docker Linux engine", engine, detail,
            "Start Docker Desktop with the WSL2/Linux container backend, then retry preflight.")
        if engine:
            ok, detail = _probe([docker, "image", "inspect", DEFAULT_IMAGE, "--format", "{{.Id}}"], 15)
            add("COLMAP image", ok, detail, f"Before the demo run: docker pull {DEFAULT_IMAGE}")
            if ok:
                gpu_args = ["--gpus", "all"] if sfm_gpu else []
                ok, detail = _probe([docker, "run", "--rm", "--pull=never", *gpu_args,
                                     DEFAULT_IMAGE, "colmap", "-h"], 45)
                add("COLMAP runtime", ok, detail,
                    "Repair Docker GPU passthrough or explicitly choose --gpu no for slower CPU camera solving.")
                if sfm_gpu and ok:
                    ok, detail = _probe([docker, "run", "--rm", "--pull=never", "--gpus", "all",
                                         DEFAULT_IMAGE, "nvidia-smi", "-L"], 30)
                    add("COLMAP GPU", ok, detail, "Repair NVIDIA Docker/WSL GPU passthrough before reconstruction.")
                elif not sfm_gpu:
                    add("COLMAP execution", False, "CPU camera solving explicitly selected; reconstruction will be slower",
                        optional=True)

    output = Path(output).resolve()
    parent = output
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    try:
        with tempfile.TemporaryFile(dir=parent) as test:
            test.write(b"vitrine-output-probe")
            test.flush()
        add("Output folder", True, "writable")
    except OSError as exc:
        add("Output folder", False, exc, "Choose a writable --run-dir on a local disk.")
    input_bytes = 0
    if source is not None and check_media:
        from .ingest import IMAGE_SUFFIXES, VIDEO_SUFFIXES
        source = Path(source)
        files = ([source] if source.is_file() else sorted(source.rglob("*"))) if source.exists() else []
        media = [p for p in files if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES | VIDEO_SUFFIXES]
        add("Input media", bool(media), f"{len(media)} supported files",
            "Select readable photographs or videos, or validate/import the capture session first.")
        invalid = []
        videos = any(p.suffix.lower() in VIDEO_SUFFIXES for p in media)
        if videos:
            for tool in ("ffmpeg", "ffprobe"):
                add(tool, bool(shutil.which(tool)), shutil.which(tool) or "not found", f"Install {tool} and add it to PATH.")
        for path in media:
            input_bytes += path.stat().st_size
            try:
                if path.suffix.lower() in IMAGE_SUFFIXES:
                    from PIL import Image
                    with Image.open(path) as image:
                        image.verify()
                elif shutil.which("ffprobe"):
                    ok, detail = _probe(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                         "-show_entries", "stream=width,height:format=duration", "-of", "json", str(path)], 20)
                    metadata = json.loads(detail) if ok else {}
                    if not metadata.get("streams") or float(metadata.get("format", {}).get("duration", 0)) <= 0:
                        raise ValueError("no video stream or finite duration")
                if path.stat().st_size == 0:
                    raise ValueError("empty file")
            except (OSError, ValueError, KeyError) as exc:
                invalid.append(f"{path.name}: {exc}")
        add("Input readability", not invalid, "; ".join(invalid[:10]) or "media headers verified",
            "Remove unreadable files from this capture selection; preserve the originals separately.")
    try:
        free = shutil.disk_usage(parent).free
        required = max(5 * 2**30, input_bytes * 5)
        add("Disk space", free >= required, f"{free/2**30:.1f} GiB free; preliminary reserve {required/2**30:.1f} GiB",
            "Choose a larger output disk. Extraction and archive copies need additional space.")
    except OSError as exc:
        add("Disk space", False, exc, "Choose an accessible output disk.")
    sidecar = os.environ.get("VITRINE_OBJECT_SIDECAR")
    add("Optional segmentation", bool(sidecar and (Path(sidecar).is_file() or shutil.which(sidecar))),
        "configured; run provider preflight to verify weights" if sidecar else "not configured; scene path remains available",
        "Configure the separate local sidecar and its weights for object isolation.", optional=True)
    return dict(ready=not any(c["status"] == "error" for c in checks), checks=checks, hardware=hardware)


def summary(report):
    rows = ["VITRINE PREFLIGHT"]
    for check in report["checks"]:
        rows.append(f"{check['name']:<24} {check['status'].upper():<7} {check['detail']}")
        if check.get("fix"):
            rows.append("  " + check["fix"])
    rows.append("READY TO RECONSTRUCT" if report["ready"] else "NOT READY — resolve the errors above")
    return "\n".join(rows)
