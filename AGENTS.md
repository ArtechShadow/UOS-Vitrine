# Vitrine — Agent Guide

Operational map for AI agents and developers working in this repository. **Read
this before changing the trainer, the SfM stage, or the hardware profiles.**
Human-facing setup lives in `README.md`; the roadmap and status live in
`docs/PROJECT-PLAN.md`.

The Python package and CLI are lowercase: `vitrine` / `python -m vitrine`.

---

## What this project is

**Vitrine** is a free, fully local, reproducible pipeline that turns video and
photographs of a physical space into a high-quality 3D Gaussian Splat, plus the
sidecar material that makes the result defensible as an act of **digital
preservation** rather than merely a nice render.

It runs on an RTX 3060 Laptop (6 GB) and scales to RTX 5090-class hardware
without changing algorithm — only the numbers in `vitrine/profiles.py`.

### Test bed

**Nested Cinema** — *Vera's Not Alone*, Dr Pavel Prokopic, University of
Salford, MediaCityUK. An immersive film installation spanning three nested
layers: traditional screens, the physical installation space, and VR.

The captured subject is the installation space: a constructed room-set with
newspaper-clad partition walls, vintage radio and reel-to-reel equipment, rugs
and a sofa, built inside a white gallery. It is a **time-limited installation**
— once de-installed it exists only as documentation, which is the whole reason
the preservation packaging matters.

### What it is *not*

Same family name, different repos — never edit those from here:

- Not the UI product. `~/Desktop/GitHub/UOS-Vitrine-Capture` (Vitrine Capture) is
  the FastAPI + React app.
- Not the lab stack. `~/Desktop/GitHub/Vitrine` (DreamLab) owns object
  segmentation, meshing ladders, Unreal export, and the consolidated Docker
  image.
- Object segmentation (SAM3D → per-object meshes → Meshy) is explicitly
  **out of scope** and deferred; it belongs to DreamLab.

---

## Hard-won facts — do not re-derive these

These cost real time to establish. Changing code that depends on them without
re-measuring will regress the project.

### 1. The GPU is a 6 GB laptop part, and VRAM is *not* the bottleneck

`nvidia-smi` reports **RTX 3060 Laptop GPU, 6144 MiB** — not the 12 GB desktop
3060. But the measured peak at 1.5M splats / 768² / SH3, forward **and**
backward, is **1.46 GB**. About a quarter of the card.

**Time is the binding constraint, not memory.** The splat cap in
`profiles.py` is a throughput control. Anyone "optimising for VRAM" here is
solving a problem that does not exist.

### 2. Rasterisation cost scales with splats *in frustum*, not total splats

Measured, RTX 3060 Laptop, 1.5M splats, 768², SH3, fwd+bwd:

| splats projecting into view | ms/iter |
|---|---|
| 94% | 500 |
| 39% | 222 |
| 15% | 95 |

A camera inside a room sees roughly a third of the scene, so interiors land
near the middle row. This is the basis of `Profile.estimated_minutes()`.

### 3. SSIM costs 9–17%, so it is always on

The reference 3DGS objective is `0.8·L1 + 0.2·(1−SSIM)`. SSIM is what
penalises blur — measured on a blurred image, SSIM collapses to **0.013** while
PSNR stays at a comfortable **10.8 dB**. An L1/MSE-only objective cannot see
the difference.

`vitrine/losses.py` implements it in **pure PyTorch, separable**. Do not
swap in `fused-ssim`: it needs its own nvcc JIT pass, and this environment
already only just manages one (see below). The separable form matters —
the square 11×11 depthwise kernel measured 60 ms/iter versus 40 ms.

### 4. The CUDA toolchain is fragile — read before touching

There is **no system CUDA toolkit**. `nvcc` comes from the pip
`nvidia-cuda-nvcc` wheel at `site-packages/nvidia/cu13/bin/nvcc`.

System GCC is **16.1.1**. CUDA 13's nvcc **cannot** compile against GCC 16
headers — it dies with `cudafe++ ... signal 11`.

`gsplat` compiles CUDA on first use and caches to
`~/.cache/torch_extensions/py314_cu130/gsplat_cuda/`. **The cached build is
already good.** But `TORCH_CUDA_ARCH_LIST`, `CC` and `CXX` are part of the
ninja command line, so *changing any of them invalidates the cache and forces a
rebuild that then fails.*

Consequences, all handled in `vitrine/cuda_toolkit.py`:

- `TORCH_CUDA_ARCH_LIST` defaults to the **detected GPU's arch** (8.6 here),
  never a wide fleet list. Widening it triggers a doomed rebuild.
- A GCC ≤ 15 must be on hand and set as `CC`/`CXX`.

**Permanent fix (recommended):**

```bash
sudo pacman -S gcc15        # provides /usr/bin/gcc-15 with its own headers
```

**Current stopgap** — point at any existing gcc-15:

```bash
export VITRINE_GCC_BIN=/home/glenn/Desktop/GitHub/UOS-Vitrine-Capture/.toolchains/gcc15/usr/bin
```

Verify with `vitrine doctor`. A correct setup imports gsplat in **~0.1 s**;
a multi-minute pause means it is rebuilding, and it will fail.

Two further traps, both now handled automatically in `cuda_toolkit.py` but
worth knowing because the error messages point somewhere unhelpful:

- **`cannot find -lcudart` at link time.** All 30 CUDA translation units
  compile, then linking fails. The pip wheels ship `libcudart.so.13` but not
  the unversioned `libcudart.so` symlink a `-dev` package would provide.
  `ensure_link_symlinks()` creates it.
- **`libcudart.so.13: cannot open shared object file` at import time**, even
  though the file exists. Setting `LD_LIBRARY_PATH` from inside Python is too
  late — the dynamic loader read it at interpreter start.
  `preload_runtime()` opens the library with `RTLD_GLOBAL` instead.

### 4b. Docker GPU passthrough — CDI can go stale after driver upgrades

The CDI spec at `/etc/cdi/nvidia.yaml` pins exact driver library versions.
A partial driver upgrade once left it referencing
`libnvidia-gtk3.so.610.43.03` while the installed file was
`libnvidia-gtk3.so.610.43.02`, so *every* GPU container failed to start —
both `--gpus all` and `--device nvidia.com/gpu=all`. COLMAP then fell back
to CPU (~36 min for 222 images vs ~5 min on GPU).

**Fixed 2026-08-05** by regenerating the spec. Verified:

- no `gtk3` entries, zero missing host paths
- `docker run --rm --gpus all … nvidia-smi` works
- `vitrine.sfm.gpu_available()` → `True`

If a future driver upgrade breaks containers again:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

`sfm.gpu_available()` still probes and falls back to CPU automatically, so a
stale CDI degrades speed, not correctness.

### 5. Multi-camera is a real requirement, not a nicety

The capture mixes 25 MP stills with 720p video frames → **two** COLMAP camera
entries with different intrinsics *and* different image dimensions.

Code that reads `cameras[0]` and applies it to every image does not error — it
silently produces a warped reconstruction. `vitrine/colmap_io.py` carries a
per-image camera reference throughout. Keep it that way.

### 6. COLMAP reconstructions have arbitrary scale

Never hard-code a world-unit threshold (densification size, export scale
clamp). Express it as a multiple of `colmap_io.scene_scale(model)`. An absolute
clamp is a bug waiting for a differently-sized room.

---

## Source data

`source/` — 457 files, all preserved, nothing deleted.

| Path | Contents | Role |
|---|---|---|
| `source/stills/` | 72 × `IMG_63xx.JPEG`, 4344×5792, iPhone 14 Pro, **full EXIF** (24 mm equiv, f/1.78) | **Master.** The archival raw material |
| `source/video/` | `IMG_6318.MP4`, 1280×720, 60 s, 1819 frames | Fills coverage gaps between stills |
| `source/luma-derivative/` | 383 × `*.jpg`, 1024×768, **EXIF-stripped**, motion-blurred | Luma AI's own keyframes. Derivative — **do not train on these** |
| `reference/` | `LumaAI_gs_Test_2.ply`, 1.27M splats, full SH3 | The cloud result to beat. Benchmark only |

`Branding/` holds XR Lab logos — leave alone.

---

## Layout

```text
UOS_Archive/
├── AGENTS.md                 ← this file
├── README.md                 ← human setup + usage
├── docs/
│   ├── PROJECT-PLAN.md       ← roadmap, status, what's next
│   ├── capture-sop.md        ← how to shoot a room so it reconstructs well
│   └── preservation.md       ← what the archive package contains and why
├── vitrine/                ← the package
│   ├── cuda_toolkit.py       ← nvcc + host-compiler discovery (read §4 above)
│   ├── profiles.py           ← laptop/workstation × draft/standard/archive
│   ├── losses.py             ← separable SSIM + photometric objective
│   ├── colmap_io.py          ← COLMAP text-model parsing, multi-camera
│   ├── ingest.py             ← frame extraction, sharpness selection   [TODO]
│   ├── sfm.py                ← COLMAP driver, multi-camera aware       [TODO]
│   ├── train.py              ← MCMC + crops + schedules + eval         [TODO]
│   ├── mesh.py               ← depth fusion → mesh                     [TODO]
│   ├── package.py            ← preservation package + manifest         [TODO]
│   └── cli.py                ← command line entry point                [TODO]
├── source/                   ← input media (see table above)
├── reference/                ← benchmark artefacts
└── runs/                     ← outputs, one directory per run
```

---

## Design decisions and their reasons

**Random-crop training.** Source images are held on CPU at 2048–2560 px; each
step renders a random 768² window by offsetting the principal point:

```
cx' = cx − x0    cy' = cy − y0    width = height = crop
```

The optimiser sees full-resolution detail while per-step cost stays bounded.
Originally motivated by VRAM; the measurements in §1 show the real benefit is
**throughput**. It is still the right design — rendering a whole 2560 px view
would cost ~11× a 768² crop — but describe it honestly.

**MCMC over the default densification strategy.** `MCMCStrategy` (present in
gsplat 1.5.3) takes a hard `cap_max`, which makes cost and memory deterministic
instead of emergent. Better quality at a fixed splat budget.

**Text COLMAP models.** The pipeline runs `model_converter` to TXT and parses
that. The archival copy is produced as a side effect of normal processing
rather than as a separate export somebody has to remember.

**A splat is a rendering, not evidence.** The preservation package keeps the
originals, the camera poses, software versions, and checksums. That is what
makes the reconstruction reproducible and auditable in ten years.

---

## Environment

| Fact | Value |
|---|---|
| OS | CachyOS (Arch), Linux 7.1.5 |
| Shell | fish |
| Python | 3.14.6 |
| torch | 2.13.0+cu130, CUDA available |
| gsplat | 1.5.3 (`MCMCStrategy`, `rasterization_2dgs`, `RGB+ED` all present) |
| GPU | RTX 3060 Laptop, 6 GB, compute capability 8.6 |
| CPU / RAM | i7-11800H, 16 threads / 16 GB |
| COLMAP | **not installed**; not in CachyOS repos. Use Docker `colmap/colmap:latest` |
| Docker | 29.7.1, `nvidia-container-toolkit` present → GPU containers work |

The project has its own venv at `.venv` — **Python 3.11**, with torch
2.11+cu128, gsplat 1.5.3 (CUDA kernels compiled and verified), pymeshlab and
trimesh. Use it:

```bash
.venv/bin/python -m vitrine ...
```

Python 3.11 rather than the system 3.14 for wheel coverage. Note that **Open3D
has no Python 3.14 wheel at all**, which is why `mesh.py` uses pymeshlab's
screened Poisson rather than the Open3D TSDF route most 3DGS meshing code
takes.

The sibling product's Python 3.14 interpreter was used during early
development and still works, but nothing depends on it.

---

## Working agreements

1. **Never edit the sibling repos** (`UOS-Vitrine-Capture`, `Vitrine`) from
   this project. They are separate products with their own owners.
2. **Never delete anything under `source/` or `reference/`.** All 457 original
   files must remain byte-identical.
3. **Measure, don't assume.** Every number in `profiles.py` came from a
   benchmark. If you change a knob, re-run the benchmark and update the
   docstring. A phase that does not improve PSNR *or* SSIM gets reverted, not
   rationalised.
4. **Keep the folder clean.** Media in `source/`, outputs in `runs/`, no loose
   files at the root.
5. **Verify against the real data**, never synthetic fixtures.
6. Prefer local, reversible changes; confirm before anything destructive.

---

## Quick commands

```bash
PY=/home/glenn/Desktop/GitHub/UOS-Vitrine-Capture/.venv/bin/python
export VITRINE_GCC_BIN=/home/glenn/Desktop/GitHub/UOS-Vitrine-Capture/.toolchains/gcc15/usr/bin

# toolchain health
$PY -c "from vitrine import cuda_toolkit; import json; print(json.dumps(cuda_toolkit.status(), indent=2))"

# profile table
$PY -c "from vitrine import profiles; [print(profiles.resolve(q,t)) for t in profiles.TIERS for q in profiles.QUALITY_LEVELS]"

# parse an existing COLMAP model
$PY -c "from vitrine.colmap_io import read_model; print(read_model('<sparse/0>').summary())"
```

Confirm gsplat loads from cache (should print in ~0.1 s, not minutes):

```bash
$PY -c "import time,vitrine.cuda_toolkit as c; c.configure(); t=time.time(); import gsplat.cuda._wrapper as w; w._make_lazy_cuda_obj('CameraModelType.PINHOLE'); print(f'{time.time()-t:.1f}s')"
```
