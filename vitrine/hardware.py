"""Hardware discovery and conservative runtime settings.

The reconstruction pipeline has two different kinds of machine information:

* facts about the host (GPU, CUDA driver, memory, CPU and system RAM), and
* safe process settings derived from those facts (worker limits and a view-cache
  budget).

This module keeps the two separate.  In particular, a capability tier is a
coarse resource class; it is not a benchmark and it never supplies a training
time estimate.  The detector deliberately has no hard dependency on PyTorch or
CUDA.  ``nvidia-smi`` is used when available and PyTorch is an optional source
of additional detail.

The public API is intentionally small::

    hardware = detect_hardware()
    runtime = resolve_runtime(hardware=hardware)

``runtime`` is suitable for callers that need worker/cache settings.  Its
``hardware`` member is the same JSON-safe record returned by
``detect_hardware``.  Manual facts can be supplied with
``VITRINE_HARDWARE_OVERRIDE`` as a JSON object or with
``VITRINE_HARDWARE_CONFIG`` pointing at a validated config file.
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import platform
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SCHEMA = "vitrine/hardware/1"
CONFIG_SCHEMA = "vitrine/hardware-config/1"
RUNTIME_SCHEMA = "vitrine/runtime/1"
CONFIG_ENV = "VITRINE_HARDWARE_CONFIG"
OVERRIDE_ENV = "VITRINE_HARDWARE_OVERRIDE"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "hardware.json"

# These are deliberately generic.  They describe resource capacity only; no
# tier below carries a throughput factor or an ETA.
CAPABILITY_TIERS = ("cpu", "constrained", "standard", "high")

HARDWARE_OVERRIDE_KEYS = frozenset(
    {
        "platform",
        "os_version",
        "architecture",
        "gpu_available",
        "cuda_available",
        "torch_cuda_available",
        "gpu_vendor",
        "gpu_model",
        "driver_version",
        "cuda_version",
        "compute_capability",
        "vram_total_gb",
        "vram_free_gb",
        "cpu_model",
        "cpu_logical_cores",
        "ram_total_gb",
        "ram_available_gb",
        "capability_tier",
    }
)

_RUNTIME_DEFAULTS: dict[str, Any] = {
    # Keep a reserve for the OS, the Python process and desktop applications.
    "ram_reserve_gb": 4.0,
    # Only this fraction of currently available RAM may be assigned to views.
    "cache_fraction": 0.50,
    # Account for temporary decode/resize buffers around the uint8 cache.
    "cache_safety_factor": 1.25,
    # These are ceilings.  The derived values also respect the host core count.
    "max_sfm_workers": 8,
    "max_io_workers": 4,
    "torch_threads": 8,
    "preview_workers": 1,
    "max_concurrent_jobs": 1,
}

_RUNTIME_KEYS = frozenset(_RUNTIME_DEFAULTS)
_COMPUTE_RE = re.compile(r"^\d{1,2}(?:\.\d{1,2})?$")
_VERSION_RE = re.compile(r"CUDA Version:\s*([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE)


class HardwareConfigError(ValueError):
    """Raised when a hardware override or runtime config is malformed."""


def _finite_number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HardwareConfigError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        raise HardwareConfigError(f"{field} must be finite and >= {minimum}")
    return number


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HardwareConfigError(f"{field} must be a non-empty string or null")
    return value.strip()


def _optional_bool(value: Any, field: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise HardwareConfigError(f"{field} must be true, false or null")
    return value


def _optional_cores(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4096:
        raise HardwareConfigError(f"{field} must be an integer between 1 and 4096 or null")
    return value


def _compute_capability(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = f"{float(value):.1f}"
    if not isinstance(value, str):
        raise HardwareConfigError("compute_capability must be a string such as '8.6' or null")
    value = value.strip()
    if not _COMPUTE_RE.fullmatch(value):
        raise HardwareConfigError("compute_capability must look like '8.6'")
    return value


def validate_hardware_override(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a copy of a flat manual hardware override.

    Unknown keys are rejected so a typo cannot silently change profile or
    worker selection.  Hardware values are intentionally flat because this is
    also the shape persisted in manifests and consumed by callers.
    """

    if not isinstance(value, Mapping):
        raise HardwareConfigError("hardware override must be a JSON object")
    unknown = sorted(set(value) - HARDWARE_OVERRIDE_KEYS)
    if unknown:
        raise HardwareConfigError(f"unknown hardware override field(s): {', '.join(unknown)}")

    out: dict[str, Any] = {}
    text_fields = {
        "platform",
        "os_version",
        "architecture",
        "gpu_vendor",
        "gpu_model",
        "driver_version",
        "cuda_version",
        "cpu_model",
    }
    bool_fields = {"gpu_available", "cuda_available", "torch_cuda_available"}
    number_fields = {"vram_total_gb", "vram_free_gb", "ram_total_gb", "ram_available_gb"}
    for field, raw in value.items():
        if field in text_fields:
            out[field] = _optional_text(raw, field)
        elif field in bool_fields:
            out[field] = _optional_bool(raw, field)
        elif field in number_fields:
            out[field] = None if raw is None else _finite_number(raw, field)
        elif field in {"cpu_logical_cores"}:
            out[field] = _optional_cores(raw, field)
        elif field == "compute_capability":
            out[field] = _compute_capability(raw)
        elif field == "capability_tier":
            if raw is not None and raw not in CAPABILITY_TIERS:
                raise HardwareConfigError(
                    f"capability_tier must be one of {CAPABILITY_TIERS}, got {raw!r}"
                )
            out[field] = raw

    total_vram = out.get("vram_total_gb")
    free_vram = out.get("vram_free_gb")
    if total_vram is not None and free_vram is not None and free_vram > total_vram:
        raise HardwareConfigError("vram_free_gb cannot exceed vram_total_gb")
    total_ram = out.get("ram_total_gb")
    available_ram = out.get("ram_available_gb")
    if total_ram is not None and available_ram is not None and available_ram > total_ram:
        raise HardwareConfigError("ram_available_gb cannot exceed ram_total_gb")
    return out


def _validate_runtime(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    if raw is None:
        return dict(_RUNTIME_DEFAULTS)
    if not isinstance(raw, Mapping):
        raise HardwareConfigError("runtime must be a JSON object")
    unknown = sorted(set(raw) - _RUNTIME_KEYS)
    if unknown:
        raise HardwareConfigError(f"unknown runtime setting(s): {', '.join(unknown)}")
    out = dict(_RUNTIME_DEFAULTS)
    for key, value in raw.items():
        if key in {"ram_reserve_gb", "cache_fraction", "cache_safety_factor"}:
            minimum = 0.0 if key == "ram_reserve_gb" else 0.01
            number = _finite_number(value, f"runtime.{key}", minimum=minimum)
            if key == "cache_fraction" and number > 1.0:
                raise HardwareConfigError("runtime.cache_fraction must be <= 1")
            if key == "cache_safety_factor" and number > 4.0:
                raise HardwareConfigError("runtime.cache_safety_factor must be <= 4")
            out[key] = number
        elif key in {
            "max_sfm_workers",
            "max_io_workers",
            "torch_threads",
            "preview_workers",
            "max_concurrent_jobs",
        }:
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 256:
                raise HardwareConfigError(f"runtime.{key} must be an integer between 1 and 256")
            out[key] = value
    return out


def _load_config(config_path: Path | str | None = None) -> tuple[Path | None, dict[str, Any]]:
    configured = config_path or os.environ.get(CONFIG_ENV)
    path = Path(configured) if configured else DEFAULT_CONFIG_PATH
    if not path.is_file():
        if configured:
            raise HardwareConfigError(f"hardware config does not exist: {path}")
        return None, {"hardware_overrides": {}, "runtime": dict(_RUNTIME_DEFAULTS)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HardwareConfigError(f"could not read hardware config {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise HardwareConfigError("hardware config must be a JSON object")
    if raw.get("schema") != CONFIG_SCHEMA:
        raise HardwareConfigError(f"hardware config schema must be {CONFIG_SCHEMA!r}")
    allowed = {"schema", "hardware_overrides", "runtime"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise HardwareConfigError(f"unknown hardware config field(s): {', '.join(unknown)}")
    hardware = validate_hardware_override(raw.get("hardware_overrides", {}))
    runtime = _validate_runtime(raw.get("runtime"))
    return path.resolve(), {"hardware_overrides": hardware, "runtime": runtime}


def _env_override() -> dict[str, Any]:
    raw = os.environ.get(OVERRIDE_ENV, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HardwareConfigError(f"{OVERRIDE_ENV} must contain a JSON object: {exc}") from exc
    return validate_hardware_override(value)


def _nvidia_smi_path() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if os.name == "nt":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\\Program Files"))
            / "NVIDIA Corporation"
            / "NVSMI"
            / "nvidia-smi.exe",
            Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "nvidia-smi.exe",
        ]
        for path in roots:
            if path.is_file():
                return str(path)
    return None


def _run_probe(command: list[str], timeout: float) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _mib(value: str) -> float | None:
    value = value.strip().replace(" MiB", "")
    try:
        number = float(value)
    except ValueError:
        return None
    return number / 1024.0 if math.isfinite(number) else None


def _probe_nvidia_smi() -> dict[str, Any]:
    executable = _nvidia_smi_path()
    if not executable:
        return {}
    query = _run_probe(
        [
            executable,
            "--query-gpu=name,driver_version,memory.total,memory.free,compute_cap",
            "--format=csv,noheader,nounits",
        ],
        5,
    )
    if query is None or query.returncode != 0:
        return {}
    line = next((item.strip() for item in query.stdout.splitlines() if item.strip()), "")
    fields = [item.strip() for item in line.split(",")]
    if len(fields) < 4:
        return {}
    result: dict[str, Any] = {
        "gpu_available": True,
        "cuda_available": True,
        "gpu_vendor": "NVIDIA",
        "gpu_model": fields[0] or None,
        "driver_version": fields[1] or None,
        "vram_total_gb": _mib(fields[2]),
        "vram_free_gb": _mib(fields[3]),
        "source_gpu": "nvidia-smi",
    }
    if len(fields) >= 5 and _COMPUTE_RE.fullmatch(fields[4]):
        result["compute_capability"] = fields[4]
    version = _run_probe([executable], 5)
    if version is not None:
        match = _VERSION_RE.search((version.stdout or "") + "\n" + (version.stderr or ""))
        if match:
            result["cuda_version"] = match.group(1)
    return result


def _probe_torch() -> dict[str, Any]:
    """Read optional PyTorch details without making it a detector dependency."""

    try:
        import torch  # type: ignore
    except (ImportError, OSError):
        return {"torch_cuda_available": None}
    try:
        available = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - optional diagnostic probe
        return {"torch_cuda_available": False}
    result: dict[str, Any] = {
        "torch_cuda_available": available,
        "cuda_version": getattr(torch.version, "cuda", None),
    }
    if not available:
        return result
    try:
        props = torch.cuda.get_device_properties(0)
        result.update(
            gpu_available=True,
            cuda_available=True,
            gpu_vendor="NVIDIA",
            gpu_model=torch.cuda.get_device_name(0),
            compute_capability=f"{props.major}.{props.minor}",
            vram_total_gb=float(props.total_memory) / 2**30,
            source_gpu="torch",
        )
        try:
            free, total = torch.cuda.mem_get_info(0)
            result["vram_free_gb"] = float(free) / 2**30
            result["vram_total_gb"] = float(total) / 2**30
        except Exception:
            logger.debug("PyTorch memory detail probe failed", exc_info=True)
    except Exception:
        logger.debug("PyTorch CUDA detail probe failed", exc_info=True)
    return result


def _cpu_model() -> str | None:
    candidates = [
        platform.processor(),
        platform.uname().processor,
        os.environ.get("PROCESSOR_IDENTIFIER", ""),
    ]
    if os.name == "nt":
        # ``platform.processor()`` is empty in some embedded Python and
        # conda builds even though Windows exposes the model in the registry.
        # Keep this optional so hardware discovery remains dependency-free.
        try:
            import winreg  # type: ignore

            key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                candidates.append(winreg.QueryValueEx(key, "ProcessorNameString")[0])
        except (ImportError, OSError):
            pass
    if os.name != "nt":
        try:
            text = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace")
            candidates.append(next((line.split(":", 1)[1].strip() for line in text.splitlines() if line.lower().startswith("model name")), ""))
        except OSError:
            pass
    return next((item.strip() for item in candidates if item and item.strip()), None)


def _ram_bytes() -> tuple[int | None, int | None, str]:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("sullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        try:
            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys), int(status.ullAvailPhys), "windows"
        except (AttributeError, OSError):
            pass
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        values: dict[str, int] = {}
        try:
            for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition(":")
                if key in {"MemTotal", "MemAvailable"}:
                    values[key] = int(value.strip().split()[0]) * 1024
            if values.get("MemTotal"):
                return values["MemTotal"], values.get("MemAvailable"), "procfs"
        except (OSError, ValueError):
            pass
    try:
        page = os.sysconf("SC_PAGE_SIZE")
        total = os.sysconf("SC_PHYS_PAGES") * page
        return int(total), None, "sysconf"
    except (AttributeError, OSError, ValueError):
        return None, None, "unknown"


def _normalise_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("gpu_model") and not snapshot.get("gpu_vendor"):
        snapshot["gpu_vendor"] = "NVIDIA"
    if snapshot.get("gpu_available") is None:
        snapshot["gpu_available"] = bool(snapshot.get("gpu_model"))
    if snapshot.get("cuda_available") is None:
        snapshot["cuda_available"] = bool(snapshot.get("gpu_available"))
    total_vram = snapshot.get("vram_total_gb")
    free_vram = snapshot.get("vram_free_gb")
    if total_vram is not None and free_vram is not None:
        snapshot["vram_free_gb"] = min(float(free_vram), float(total_vram))
    total_ram = snapshot.get("ram_total_gb")
    available_ram = snapshot.get("ram_available_gb")
    if total_ram is not None and available_ram is not None:
        snapshot["ram_available_gb"] = min(float(available_ram), float(total_ram))
    return snapshot


def classify_capability(hardware: Mapping[str, Any]) -> str:
    """Return a generic resource tier without promising a throughput rate."""

    manual = hardware.get("capability_tier")
    if manual in CAPABILITY_TIERS:
        return str(manual)
    if not hardware.get("gpu_available"):
        return "cpu"
    vram = hardware.get("vram_total_gb")
    if not isinstance(vram, (int, float)):
        return "standard"
    if vram < 8:
        return "constrained"
    if vram < 20:
        return "standard"
    return "high"


def detect_hardware(
    *,
    config_path: Path | str | None = None,
    override: Mapping[str, Any] | None = None,
    probe: bool = True,
) -> dict[str, Any]:
    """Return a JSON-safe snapshot of the current machine.

    ``probe=False`` is useful for deterministic callers that only want the
    manual/config values plus CPU/RAM facts.  The normal path is still safe on
    a machine without CUDA: missing ``nvidia-smi`` and missing PyTorch simply
    produce null GPU fields.
    """

    path, config = _load_config(config_path)
    snapshot: dict[str, Any] = {
        "schema": SCHEMA,
        "platform": platform.system() or None,
        "os_version": platform.version() or platform.release() or None,
        "architecture": (
            platform.machine()
            or os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE")
            or ("x64" if ctypes.sizeof(ctypes.c_void_p) == 8 else "x86")
        ),
        "gpu_available": False,
        "cuda_available": False,
        "torch_cuda_available": None,
        "gpu_vendor": None,
        "gpu_model": None,
        "driver_version": None,
        "cuda_version": None,
        "compute_capability": None,
        "vram_total_gb": None,
        "vram_free_gb": None,
        "cpu_model": _cpu_model(),
        "cpu_logical_cores": os.cpu_count() or 1,
        "ram_total_gb": None,
        "ram_available_gb": None,
        "source_gpu": None,
        "source_ram": None,
    }
    total_ram, available_ram, ram_source = _ram_bytes()
    if total_ram is not None:
        snapshot["ram_total_gb"] = total_ram / 2**30
    if available_ram is not None:
        snapshot["ram_available_gb"] = available_ram / 2**30
    snapshot["source_ram"] = ram_source

    if probe:
        # PyTorch is intentionally secondary.  nvidia-smi works in a clean
        # environment where torch is not installed and is the better source for
        # driver-reported free VRAM.
        snapshot.update(_probe_nvidia_smi())
        torch_values = _probe_torch()
        for key, value in torch_values.items():
            if value is not None and (snapshot.get(key) in (None, False) or key in {"torch_cuda_available"}):
                snapshot[key] = value

    merged: dict[str, Any] = {}
    merged.update(config.get("hardware_overrides", {}))
    merged.update(_env_override())
    if override is not None:
        merged.update(validate_hardware_override(override))
    if merged:
        snapshot.update(merged)
        # A compact manual override often supplies only the model or VRAM.
        # Infer availability in that case, while preserving an explicit false
        # value for diagnostics that intentionally disable CUDA.
        if "gpu_available" not in merged and merged.get("gpu_model"):
            snapshot["gpu_available"] = True
        if "cuda_available" not in merged and merged.get("gpu_model"):
            snapshot["cuda_available"] = True
    snapshot = _normalise_snapshot(snapshot)
    snapshot["capability_tier"] = classify_capability(snapshot)
    snapshot["config_path"] = str(path) if path else None
    snapshot["overrides"] = sorted(merged)
    return snapshot


def resolve_runtime(
    *,
    hardware: Mapping[str, Any] | None = None,
    config_path: Path | str | None = None,
    override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve worker, cache and preview settings from a hardware snapshot.

    The returned values are intentionally conservative and contain no
    benchmark-derived throughput or ETA.  ``workers.sfm`` is a ceiling for
    native SfM work; callers may still lower it for a shared machine.
    """

    path, config = _load_config(config_path)
    if hardware is None:
        snapshot = detect_hardware(config_path=config_path, override=override)
    else:
        snapshot = dict(hardware)
        if override is not None:
            snapshot.update(validate_hardware_override(override))
        # Validate the fields that callers might pass from a persisted record.
        fields = {key: snapshot[key] for key in snapshot if key in HARDWARE_OVERRIDE_KEYS}
        snapshot = _normalise_snapshot(validate_hardware_override(fields))
        snapshot.setdefault("schema", SCHEMA)
        snapshot.setdefault("capability_tier", classify_capability(snapshot))

    tier = classify_capability(snapshot)
    cores = int(snapshot.get("cpu_logical_cores") or 1)
    runtime_cfg = config["runtime"]
    sfm_workers = min(runtime_cfg["max_sfm_workers"], max(1, cores - 2 if cores > 2 else 1))
    io_workers = min(runtime_cfg["max_io_workers"], max(1, cores // 4 or 1))
    torch_threads = min(runtime_cfg["torch_threads"], max(1, cores))

    available = snapshot.get("ram_available_gb")
    total = snapshot.get("ram_total_gb")
    if available is None:
        available = total
    reserve = float(runtime_cfg["ram_reserve_gb"])
    cache_fraction = float(runtime_cfg["cache_fraction"])
    if isinstance(available, (int, float)) and math.isfinite(float(available)):
        available = max(0.0, float(available))
        cache_budget = max(0.0, min(available * cache_fraction, available - reserve))
    else:
        cache_budget = None

    # ``legacy_tier`` is a compatibility bridge for the existing six profile
    # names.  The generic tier remains the authoritative hardware description.
    legacy_tier = "workstation" if tier == "high" else "laptop"
    return {
        "schema": RUNTIME_SCHEMA,
        "capability_tier": tier,
        "legacy_tier": legacy_tier,
        "hardware": snapshot,
        "workers": {
            "sfm": sfm_workers,
            "io": io_workers,
            "torch": torch_threads,
            "preview": runtime_cfg["preview_workers"],
            "max_concurrent_jobs": runtime_cfg["max_concurrent_jobs"],
        },
        "cache": {
            "ram_total_gb": total,
            "ram_available_gb": available,
            "ram_reserve_gb": reserve,
            "fraction": cache_fraction,
            "safety_factor": float(runtime_cfg["cache_safety_factor"]),
            "view_cache_budget_gb": cache_budget,
        },
        "gpu": {
            "available": bool(snapshot.get("gpu_available")),
            "cuda_available": bool(snapshot.get("cuda_available")),
            "torch_cuda_available": snapshot.get("torch_cuda_available"),
            "vram_total_gb": snapshot.get("vram_total_gb"),
            "vram_free_gb": snapshot.get("vram_free_gb"),
        },
        "config_path": str(path) if path else None,
    }


__all__ = [
    "CAPABILITY_TIERS",
    "CONFIG_SCHEMA",
    "DEFAULT_CONFIG_PATH",
    "HARDWARE_OVERRIDE_KEYS",
    "RUNTIME_SCHEMA",
    "HardwareConfigError",
    "classify_capability",
    "detect_hardware",
    "resolve_runtime",
    "validate_hardware_override",
]
