"""Wire up the CUDA toolchain that gsplat's JIT compiler needs.

gsplat ships CUDA source and compiles it on first use with ``nvcc``. Two things
have to be true for that to work here, and neither is guaranteed:

1. **nvcc must exist.** There is no system CUDA toolkit — only the driver.
   nvcc comes from the pip ``nvidia-cuda-nvcc`` wheel, which lands in
   ``site-packages/nvidia/cu13`` and is not on PATH.

2. **A host compiler nvcc accepts must exist.** On Linux that means a GCC
   version nvcc hasn't blacklisted (CUDA 13 refuses anything newer than
   GCC 15). On Windows it means MSVC's ``cl.exe``, which normally requires
   launching from a Visual Studio Developer Prompt — something a plain
   ``python -m vitrine ...`` invocation never does.

Once the extension is built it is cached under
``~/.cache/torch_extensions/py<ver>_cu<ver>/gsplat_cuda`` and later runs only
need the environment variables set, not a working compiler. That is why a
broken toolchain can still appear to work until the cache is cleared.

This module was originally Linux-only (developed on Arch/CachyOS against an
RTX 3060 Laptop) and has since been ported to also run on Windows (RTX 5090
workstation). ``configure()`` branches on the OS; the two toolchains are
different enough that there is little to share beyond nvcc discovery and the
GPU-architecture defaulting.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import sysconfig
from pathlib import Path

logger = logging.getLogger(__name__)

IS_WINDOWS = platform.system() == "Windows"

# Architectures we could build for: 8.6 = Ampere (RTX 3060), 8.9 = Ada
# (RTX 4090), 12.0 = Blackwell (RTX 5090).
#
# We deliberately do NOT default to all three. TORCH_CUDA_ARCH_LIST is part of
# the extension's build hash, so widening it invalidates any cached build and
# forces a fresh nvcc run — which is exactly when a marginal host-compiler
# setup blows up. Default to the arch of the GPU actually present so a machine
# that has already built gsplat once never rebuilds it. Set the env var
# explicitly to cross-compile for a fleet.
FLEET_ARCH_LIST = "8.6;8.9;12.0"

# Compilers nvcc will accept, newest first.
_HOST_COMPILER_CANDIDATES = (("gcc-15", "g++-15"), ("gcc-14", "g++-14"), ("gcc-13", "g++-13"))


def local_arch() -> str | None:
    """CUDA compute capability of the attached GPU, e.g. ``"8.6"``.

    Returns ``None`` if torch or CUDA is unavailable, in which case the caller
    should fall back to an explicit list.
    """
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    major, minor = torch.cuda.get_device_capability(0)
    return f"{major}.{minor}"


_NVCC_NAME = "nvcc.exe" if IS_WINDOWS else "nvcc"


def find_nvcc() -> Path | None:
    """Locate a usable CUDA toolkit root (a directory containing ``bin/nvcc``)."""
    configured = os.environ.get("CUDA_HOME")
    if configured and (Path(configured) / "bin" / _NVCC_NAME).is_file():
        return Path(configured)

    # pip wheel layout: site-packages/nvidia/cu13/bin/nvcc[.exe]
    site_packages = Path(sysconfig.get_paths()["purelib"])
    for cuda_dir in sorted((site_packages / "nvidia").glob("cu*"), reverse=True):
        if (cuda_dir / "bin" / _NVCC_NAME).is_file():
            return cuda_dir

    # A real system toolkit, if one ever gets installed.
    system_roots = (
        (Path(os.environ.get("CUDA_PATH", "")),)
        if IS_WINDOWS
        else (Path("/opt/cuda"), Path("/usr/local/cuda"))
    )
    for root in system_roots:
        if root and (root / "bin" / _NVCC_NAME).is_file():
            return root

    return None


def _pair_in(root: Path) -> tuple[Path, Path] | None:
    for cc_name, cxx_name in _HOST_COMPILER_CANDIDATES:
        cc, cxx = root / cc_name, root / cxx_name
        if cc.is_file() and cxx.is_file():
            return cc, cxx
    return None


def find_host_compiler(extra_search: list[Path] | None = None) -> tuple[Path, Path] | None:
    """Find a ``(gcc, g++)`` pair old enough for nvcc. **Linux only.**

    Order: ``VITRINE_GCC_BIN`` → PATH → ``/usr/bin`` → caller-supplied roots
    (vendored toolchains). Returns ``None`` if nothing suitable is found, which
    callers should read as "leave CC/CXX alone".

    Setting these matters even when the extension is already compiled: they
    form part of the ninja build command line, so a mismatch makes ninja
    consider the cached build stale and rebuild it — turning a 0.1 s import
    into a multi-minute compile that then fails.
    """
    override = os.environ.get("VITRINE_GCC_BIN")
    if override:
        found = _pair_in(Path(override))
        if found:
            return found
        logger.warning("VITRINE_GCC_BIN=%s contains no usable gcc/g++ pair", override)

    for cc_name, cxx_name in _HOST_COMPILER_CANDIDATES:
        cc, cxx = shutil.which(cc_name), shutil.which(cxx_name)
        if cc and cxx:
            return Path(cc), Path(cxx)

    for root in [Path("/usr/bin"), *(extra_search or [])]:
        found = _pair_in(root)
        if found:
            return found

    return None


def find_vs_installation() -> Path | None:
    """Locate a Visual Studio install with the C++ (MSVC) workload. **Windows only.**

    Uses ``vswhere``, which Visual Studio installers have placed at a fixed
    path since VS2017 specifically so tools don't have to guess. Returns the
    installation root (e.g. ``...\\Microsoft Visual Studio\\18\\BuildTools``).
    """
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(program_files_x86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.is_file():
        return None
    try:
        result = subprocess.run(
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError:
        return None
    path = result.stdout.strip()
    return Path(path) if path else None


def find_msvc_toolset(vs_root: Path) -> str | None:
    """Newest MSVC toolset directory under ``vs_root`` with ``cl.exe`` on disk.

    Returns the full version (e.g. ``"14.44.35207"``), not just major.minor —
    callers that need ``-vcvars_ver`` should take the first two components.

    Deliberately does not trust VS's own default-toolset selection
    (``Microsoft.VCToolsVersion.default.txt``): on this machine's VS "18"
    (2026 preview) install, the default pointed at version file
    ``14.50.18.0`` while the real toolset directory was ``14.50.35717`` — a
    naming mismatch that made a bare ``vcvarsall.bat x64`` silently produce a
    PATH with no ``cl.exe`` on it at all. Scanning the toolset directories
    directly and pinning ``-vcvars_ver`` to one verified to exist sidesteps
    that instead of trusting the default.
    """
    msvc_root = vs_root / "VC" / "Tools" / "MSVC"
    if not msvc_root.is_dir():
        return None
    versions = sorted((d.name for d in msvc_root.iterdir() if d.is_dir()), reverse=True)
    for version in versions:
        if (msvc_root / version / "bin" / "Hostx64" / "x64" / "cl.exe").is_file():
            return version
    return None


def msvc_environment(vs_root: Path, toolset: str | None) -> dict[str, str] | None:
    """Run ``vcvarsall.bat`` and capture the environment it sets up. **Windows only.**

    ``vcvarsall.bat`` only mutates the environment of the ``cmd.exe`` that
    runs it — there is no way to source it into a running Python process, so
    the standard trick (also used internally by ``setuptools``) is to chain a
    ``set`` after it in the same ``cmd`` invocation and parse the dump.
    """
    vcvarsall = vs_root / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
    if not vcvarsall.is_file():
        return None
    command = f'"{vcvarsall}" x64'
    if toolset:
        command += f" -vcvars_ver={toolset}"
    try:
        result = subprocess.run(
            f'cmd /c "{command} && set"',
            capture_output=True,
            text=True,
            shell=True,
            timeout=60,
            check=False,
        )
    except OSError:
        return None
    env: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            # ``set`` preserves the casing used by the current environment.
            # On Windows 11 this commonly emits ``Path`` rather than ``PATH``;
            # treating keys case-sensitively silently drops the MSVC search
            # path even though INCLUDE/LIB were captured correctly.
            env[key.upper()] = value
    return env if "INCLUDE" in env else None


def ensure_link_symlinks(cuda_root: Path) -> list[str]:
    """Create the ``libfoo.so`` symlinks the linker needs.

    The pip CUDA wheels ship versioned runtime libraries (``libcudart.so.13``)
    but not the unversioned development symlinks (``libcudart.so``) that a
    system ``-dev`` package would provide. Compiling gsplat therefore gets all
    the way through 30 CUDA translation units and then fails at the final link
    with ``cannot find -lcudart`` — which reads like a missing dependency but
    is really a missing symlink.

    Returns the names of any links created, for logging.
    """
    lib_dir = cuda_root / "lib"
    if not lib_dir.is_dir():
        return []

    created: list[str] = []
    for stem in ("libcudart", "libcublas", "libcublasLt", "libcurand", "libcusparse", "libcusolver"):
        target = lib_dir / f"{stem}.so"
        if target.exists() or target.is_symlink():
            continue
        versioned = sorted(lib_dir.glob(f"{stem}.so.*"))
        if not versioned:
            continue
        try:
            target.symlink_to(versioned[-1].name)
            created.append(target.name)
        except OSError as exc:  # read-only install, or a race with another process
            logger.debug("could not create %s: %s", target, exc)

    if created:
        logger.info("created linker symlinks in %s: %s", lib_dir, ", ".join(created))
    return created


def preload_runtime(cuda_root: Path) -> list[str]:
    """Load the CUDA runtime into the process before the extension needs it.

    Setting ``LD_LIBRARY_PATH`` from inside Python is too late: the dynamic
    loader read it when the interpreter started, so a freshly compiled
    extension still fails to import with ``libcudart.so.13: cannot open shared
    object file`` even though the file is right there.

    Opening the library explicitly with ``RTLD_GLOBAL`` puts its symbols in the
    global namespace, which satisfies the extension's own load. This is what
    makes the pipeline work without the caller having to export environment
    variables before launching Python.
    """
    import ctypes

    lib_dir = cuda_root / "lib"
    if not lib_dir.is_dir():
        return []

    loaded: list[str] = []
    for stem in ("libcudart",):
        candidates = sorted(lib_dir.glob(f"{stem}.so.*"))
        if not candidates:
            continue
        try:
            ctypes.CDLL(str(candidates[-1]), mode=ctypes.RTLD_GLOBAL)
            loaded.append(candidates[-1].name)
        except OSError as exc:
            logger.debug("could not preload %s: %s", candidates[-1], exc)

    return loaded


def _configure_linux(cuda_root: Path, vendored_toolchains: list[Path] | None) -> None:
    os.environ["LD_LIBRARY_PATH"] = f"{cuda_root / 'lib'}{os.pathsep}{os.environ.get('LD_LIBRARY_PATH', '')}"

    # Two parallel nvcc jobs: developed on a 16-thread/16 GB laptop where each
    # nvcc pass is memory-hungry enough that more jobs than that thrashes.
    os.environ.setdefault("MAX_JOBS", "2")
    os.environ.setdefault("NVCC_PREPEND_FLAGS", "--allow-unsupported-compiler")

    ensure_link_symlinks(cuda_root)
    preload_runtime(cuda_root)

    compiler = find_host_compiler(vendored_toolchains)
    if compiler is not None:
        cc, cxx = compiler
        os.environ.setdefault("CC", str(cc))
        os.environ.setdefault("CXX", str(cxx))
        logger.debug("host compiler for nvcc: %s", cc)


def patch_gsplat_msvc_cflags() -> None:
    """Strip a GCC-only flag gsplat hardcodes into every JIT build. **Windows only.**

    ``gsplat/cuda/_backend.py`` unconditionally sets
    ``extra_cflags = [opt_level, "-Wno-attributes"]`` for the plain ``.cpp``
    sources it compiles directly with the host compiler (the ``.cu`` sources
    go through nvcc, which translates ``-Xcompiler`` flags fine — this is only
    the never-nvcc-wrapped case). ``-Wno-attributes`` is meaningless to
    ``cl.exe``, which rejects it outright: ``Command line error D8021:
    invalid numeric argument '/Wno-attributes'``.

    gsplat imports ``_jit_compile`` by name (``from torch.utils.cpp_extension
    import _jit_compile``) inside the same module that both builds the flag
    list and calls it, at *import* time — so there is no seam to patch
    ``gsplat`` itself after the fact. Patching ``torch.utils.cpp_extension``
    instead works because gsplat's ``import`` just copies whatever binding is
    there at that moment; this must therefore run before gsplat is imported
    anywhere in the process.
    """
    import torch.utils.cpp_extension as cpp_extension

    if getattr(cpp_extension, "_vitrine_msvc_cflags_patch", False):
        return

    original_jit_compile = cpp_extension._jit_compile

    def _jit_compile_without_gcc_flags(name, sources, extra_cflags, *args, **kwargs):
        if extra_cflags:
            extra_cflags = [f for f in extra_cflags if f != "-Wno-attributes"]
        return original_jit_compile(name, sources, extra_cflags, *args, **kwargs)

    cpp_extension._jit_compile = _jit_compile_without_gcc_flags
    cpp_extension._vitrine_msvc_cflags_patch = True


# (old, new) source substitutions keyed by the .cu file they apply to. Each
# fixes a gsplat __INS__ explicit-instantiation macro that redeclares a
# trailing output parameter as `const` when the real definition (and the
# header every other TU calls through) declares it non-`const`. See
# patch_gsplat_msvc_source for why this only breaks MSVC.
_GSPLAT_MSVC_SOURCE_FIXES: dict[str, list[tuple[str, str]]] = {
    "RasterizeToPixels2DGSBwd.cu": [
        (
            "        at::optional<at::Tensor> v_means2d_abs,                                \\\n"
            "        const at::Tensor v_means2d,                                            \\\n"
            "        const at::Tensor v_ray_transforms,                                     \\\n"
            "        const at::Tensor v_colors,                                             \\\n"
            "        const at::Tensor v_opacities,                                          \\\n"
            "        const at::Tensor v_normals,                                            \\\n"
            "        const at::Tensor v_densify                                             \\\n",
            "        at::optional<at::Tensor> v_means2d_abs,                                \\\n"
            "        at::Tensor v_means2d,                                                  \\\n"
            "        at::Tensor v_ray_transforms,                                          \\\n"
            "        at::Tensor v_colors,                                                  \\\n"
            "        at::Tensor v_opacities,                                               \\\n"
            "        at::Tensor v_normals,                                                 \\\n"
            "        at::Tensor v_densify                                                  \\\n",
        ),
    ],
    "RasterizeToPixelsFromWorld3DGSFwd.cu": [
        (
            "        const UnscentedTransformParameters ut_params,                         \\\n"
            "        const ShutterType rs_type,                                             \\\n",
            "        const UnscentedTransformParameters ut_params,                         \\\n"
            "        ShutterType rs_type,                                                  \\\n",
        ),
        (
            "        const at::Tensor tile_offsets,                                         \\\n"
            "        const at::Tensor flatten_ids,                                          \\\n"
            "        const at::Tensor renders,                                              \\\n"
            "        const at::Tensor alphas,                                               \\\n"
            "        const at::Tensor last_ids                                               \\\n",
            "        const at::Tensor tile_offsets,                                         \\\n"
            "        const at::Tensor flatten_ids,                                          \\\n"
            "        at::Tensor renders,                                                    \\\n"
            "        at::Tensor alphas,                                                     \\\n"
            "        at::Tensor last_ids                                                     \\\n",
        ),
    ],
}


def patch_gsplat_msvc_source() -> list[str]:
    """Fix a gsplat source bug that only breaks the link step under MSVC. **Windows only.**

    Two of gsplat's ``__INS__`` explicit-instantiation macros redeclare their
    trailing output ``at::Tensor`` (and, in one case, a ``ShutterType``)
    parameters as ``const``, while the real function definition — and the
    header every calling ``.cpp`` compiles against — declare them non-const.
    Top-level ``const`` on a by-value parameter isn't part of a function's
    type per the C++ standard, and GCC/Itanium mangles both spellings
    identically, so this is invisible on Linux (where gsplat is developed and
    tested). MSVC's mangler does not collapse them: the explicit instantiation
    (built from the ``__INS__`` macro's, wrong, spelling) and the call site
    (built from the header's, correct, spelling) end up with different
    decorated names, and the linker reports every CDIM of
    ``launch_rasterize_to_pixels_2dgs_bwd_kernel`` and
    ``launch_rasterize_to_pixels_from_world_3dgs_fwd_kernel`` as an
    unresolved external symbol — despite both translation units compiling
    without a single warning.

    Patches the installed wheel's ``.cu`` sources in place. Idempotent: a
    ``pip install --upgrade gsplat`` will restore the bug, but the next
    ``configure()`` call reapplies the patch before anything imports gsplat.
    Silently does nothing to a file whose text no longer matches — read as
    "gsplat shipped something else here", not "already patched".
    """
    import gsplat

    csrc = Path(gsplat.__file__).parent / "cuda" / "csrc"
    patched: list[str] = []
    for filename, fixes in _GSPLAT_MSVC_SOURCE_FIXES.items():
        path = csrc / filename
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        for old, new in fixes:
            text = text.replace(old, new)
        if text != original:
            path.write_text(text, encoding="utf-8")
            patched.append(filename)
    if patched:
        logger.debug("patched gsplat MSVC const-mismatch bug in: %s", ", ".join(patched))
    return patched


def _configure_windows(cuda_root: Path) -> None:
    # The pip wheel splits nvcc (bin/) from the runtime DLL (bin/x86_64/); both
    # need to be resolvable, the former for compiling, the latter for loading
    # the extension afterwards. Unlike Linux there is no LD_LIBRARY_PATH/RTLD
    # dance needed — Windows resolves DLLs against PATH at load time, so
    # putting the DLL's directory on PATH is sufficient by itself.
    os.environ["PATH"] = f"{cuda_root / 'bin' / 'x86_64'}{os.pathsep}{os.environ.get('PATH', '')}"

    # More headroom than the laptop's MAX_JOBS=2: same core count, roughly
    # double the RAM.
    os.environ.setdefault("MAX_JOBS", "4")

    # NVIDIA's CCCL headers (bundled with the nvcc wheel) refuse to compile
    # under cl.exe's legacy preprocessor and hard-error unless told to use
    # the standard-conforming one.
    prepend = os.environ.get("NVCC_PREPEND_FLAGS", "")
    if "/Zc:preprocessor" not in prepend:
        os.environ["NVCC_PREPEND_FLAGS"] = f"{prepend} -Xcompiler /Zc:preprocessor".strip()

    patch_gsplat_msvc_cflags()
    patch_gsplat_msvc_source()

    if shutil.which("cl.exe"):
        return  # already running from a Developer Prompt; leave PATH/INCLUDE/LIB alone

    vs_root = find_vs_installation()
    if vs_root is None:
        logger.warning("no Visual Studio C++ toolset found; nvcc will fail without cl.exe on PATH")
        return

    toolset = find_msvc_toolset(vs_root)
    vcvars_ver = ".".join(toolset.split(".")[:2]) if toolset else None
    env = msvc_environment(vs_root, vcvars_ver)
    if env is None:
        logger.warning("could not initialise MSVC environment from %s (toolset=%s)", vs_root, toolset)
        return

    for key in ("PATH", "INCLUDE", "LIB", "LIBPATH"):
        if key in env:
            os.environ[key] = env[key]
    os.environ.setdefault("CC", "cl.exe")
    os.environ.setdefault("CXX", "cl.exe")
    logger.debug("MSVC toolset %s from %s", toolset, vs_root)


def configure(vendored_toolchains: list[Path] | None = None) -> Path | None:
    """Set the environment gsplat's JIT needs. Returns the CUDA root, or None.

    Safe to call repeatedly; existing values are respected.
    """
    cuda_root = find_nvcc()
    if cuda_root is None:
        return None

    os.environ["CUDA_HOME"] = str(cuda_root)
    os.environ["PATH"] = f"{cuda_root / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ.setdefault("TORCH_CUDA_ARCH_LIST", local_arch() or FLEET_ARCH_LIST)

    if IS_WINDOWS:
        _configure_windows(cuda_root)
    else:
        _configure_linux(cuda_root, vendored_toolchains)

    return cuda_root


def status() -> dict[str, object]:
    """Report toolchain state for diagnostics — used by ``vitrine doctor``."""
    cuda_root = find_nvcc()
    if IS_WINDOWS:
        compiler_path = shutil.which("cl.exe")
        if compiler_path is None:
            vs_root = find_vs_installation()
            toolset = find_msvc_toolset(vs_root) if vs_root else None
            if vs_root and toolset:
                compiler_path = str(vs_root / "VC" / "Tools" / "MSVC" / toolset / "bin" / "Hostx64" / "x64" / "cl.exe")
    else:
        compiler = find_host_compiler()
        compiler_path = str(compiler[0]) if compiler else None
    # Windows: %LOCALAPPDATA%\torch_extensions\torch_extensions\Cache\<tag>\gsplat_cuda
    # Linux:   ~/.cache/torch_extensions/<tag>/gsplat_cuda
    cache = (
        Path(os.environ.get("LOCALAPPDATA", Path.home())) / "torch_extensions"
        if IS_WINDOWS
        else Path.home() / ".cache" / "torch_extensions"
    )
    built = sorted(p.parent.name for p in cache.rglob("gsplat_cuda")) if cache.is_dir() else []
    return {
        "cuda_root": str(cuda_root) if cuda_root else None,
        "nvcc": str(cuda_root / "bin" / _NVCC_NAME) if cuda_root else None,
        "host_compiler": compiler_path,
        "gsplat_prebuilt": bool(built),
        "arch_list": os.environ.get("TORCH_CUDA_ARCH_LIST") or local_arch() or FLEET_ARCH_LIST,
    }
