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
preservation** — a digital twin of a moment, not a live system — rather than
merely a nice render.

It runs on an RTX 3060 Laptop (6 GB) and scales to RTX 5090-class hardware
without changing algorithm — only the numbers in `vitrine/profiles.py`.

Public repo: [github.com/ArtechShadow/UOS-Vitrine](https://github.com/ArtechShadow/UOS-Vitrine)
(MIT). Developed in collaboration with **DreamLab AI** — see "Related, separate
repos" below for the boundary between the two.

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
- Not the lab stack. `~/Desktop/GitHub/Vitrine` ([DreamLab-AI/Vitrine](https://github.com/DreamLab-AI/Vitrine),
  GPL-3.0) owns object segmentation, meshing ladders, Unreal export, and the
  consolidated Docker image.
- Object segmentation (SAM3D → per-object meshes → Meshy) is explicitly
  **out of scope** and deferred; it belongs to DreamLab.

---

## Hard-won facts — do not re-derive these

These cost real time to establish. Changing code that depends on them without
re-measuring will regress the project.

### 1. The GPU is a 6 GB laptop part, and VRAM is *not* the bottleneck (laptop tier)

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

The RTX 5090 is far flatter across the same range (14.4/13.9/12.2 ms — see
`profiles.py`'s module docstring) because at 12–14 ms/iter, Python-loop and
kernel-launch overhead — which doesn't shrink with the GPU — dominates. The
"cost scales with visible fraction" model is already an approximation on
that card, not a law.

### 3. SSIM costs 9–17%, so it is always on

The reference 3DGS objective is `0.8·L1 + 0.2·(1−SSIM)`. SSIM is what
penalises blur — measured on a blurred image, SSIM collapses to **0.013** while
PSNR stays at a comfortable **10.8 dB**. An L1/MSE-only objective cannot see
the difference.

`vitrine/losses.py` implements it in **pure PyTorch, separable**. Do not
swap in `fused-ssim`: it needs its own nvcc JIT pass, and the CUDA toolchain
already only just manages one build (see §4/§4-Windows below). The separable
form matters — the square 11×11 depthwise kernel measured 60 ms/iter versus
40 ms.

### 4. Lens distortion was never corrected — the "crop causes collapse" bug, solved 2026-08-08

**The single largest quality fix on this project.** Every camera COLMAP
solves for this capture comes back `OPENCV` with real radial distortion
(k1 ≈ 0.06–0.07 for the phone cameras). `colmap_io.Camera` exposed a
`has_distortion` property; **nothing ever called it.** `gsplat.rasterization`
is a pinhole projector, so every training run had been fitting a pinhole
camera to photographs taken through a lens that is not one — a 13 px error in
the corners of the main stills camera at working resolution.

Fixed in `vitrine/undistort.py`, applied once per view at load in
`dataset.ViewSet` (`undistort=True` by default — leave it on). One variable,
otherwise identical, 736 views: **+4.93 dB PSNR, +0.155 SSIM, live Gaussians
6.3% → 42.6%.**

This is also why big crops of big sources used to look actively harmful: lens
distortion error grows with distance from the principal point, so a bigger
crop of a bigger source reaches further into the uncorrected corners. Small
crops mostly sampled the middle of the frame, where the lens is nearly
pinhole, and looked "healthy" for that reason alone. **With the fix in place
the relationship inverts — larger crops help, once the lens is accounted
for.** Full record: `docs/undistortion-finding.md`.

Every quality number published before 2026-08-08 — including the 25.72 dB
`nested-cinema-01` laptop baseline — was measured with this bug active. That
baseline is not wrong as a record, but it is not a ceiling.

### 5. Frame coverage — not resolution, not `cap_max` — is what bounds a crop, independent of §4

A Gaussian receives reconstruction gradient only on steps where it falls
inside the sampled random crop, but `OPACITY_REG`/`SCALE_REG` pull on *every*
Gaussian on *every* step. The fraction of a frame a crop covers is therefore
the ratio of signal to regularisation, and it is a **second, independent**
control variable from the distortion bug above — fixing distortion does not
exempt a run from this rule:

| source | crop | coverage | PSNR | alive |
|---|---|---|---|---|
| 1600 | 1024 | 64% | 22.71 | 68% |
| 2304 | 1536 | 67% | 22.82 | 70% |
| 3072 | 1536 | 45% | 12.52 | 3% |
| 2304 | 1024 | 35% | 10.51 | 3% |

`train.py`'s `MIN_SAFE_COVERAGE = 0.5` computes this ratio and warns below it.
Raising `source_long_edge` while holding `crop` fixed silently lowers
coverage — that is what makes "train at higher resolution" look like it
breaks training. It doesn't break it, it starves it. Full record:
`docs/nested-cinema-04-master.md`.

**Consequence for `profiles.py`: `WORKSTATION_ARCHIVE` (`crop=1600` /
`source_long_edge=4096`, 39% coverage) is still unvalidated as of this
writing** — below the safe-coverage line regardless of the distortion fix.
Do not assume it works because §4 is fixed; measure it. The validated
high-quality recipe (`source_long_edge=2304` / `crop=1536`, cap 2M, 67%
coverage, 22.9 dB / 0.778 SSIM on 736 views) currently only exists as
`scripts/run_nested_cinema_04_master.py`, not folded into `profiles.py` yet.
Working agreement #3 applies directly here: an unvalidated number in a shipped
profile gets measured or reverted, not left in place quietly.

### 6. The CUDA toolchain is fragile on both platforms — read before touching

There is **no system CUDA toolkit** on either OS. `nvcc` comes from the pip
`nvidia-cuda-nvcc` wheel. `gsplat` JIT-compiles its CUDA extension on first
use and caches it; **the cached build is already good**, but the compiler
flags/arch list are part of the cache key, so *changing any of them
invalidates the cache and forces a rebuild that can then fail.*

#### Linux (original dev machine — CachyOS/Arch)

System GCC is **16.1.1**. CUDA 13's nvcc **cannot** compile against GCC 16
headers — dies with `cudafe++ ... signal 11`. Consequences, all handled in
`vitrine/cuda_toolkit.py`:

- `TORCH_CUDA_ARCH_LIST` defaults to the **detected GPU's arch**, never a wide
  fleet list — widening it triggers a doomed rebuild.
- A GCC ≤ 15 must be on hand and set as `CC`/`CXX`. Permanent fix:
  `sudo pacman -S gcc15`. Stopgap: `export VITRINE_GCC_BIN=<path to a gcc-15 bin dir>`.
- **`cannot find -lcudart` at link time** — the pip wheels ship
  `libcudart.so.13` but not the unversioned symlink a `-dev` package would
  provide. `ensure_link_symlinks()` creates it.
- **`libcudart.so.13: cannot open shared object file` at import time**, even
  though the file exists — setting `LD_LIBRARY_PATH` from Python is too late,
  the dynamic loader already read it. `preload_runtime()` opens the library
  with `RTLD_GLOBAL` instead.

Verify with `vitrine doctor`. A correct setup imports gsplat in **~0.1 s**; a
multi-minute pause means it is rebuilding, and it will likely fail.

#### Windows (current active machine — Windows 11, RTX 5090)

Host compiler is **MSVC**, not GCC — a full parallel path in
`cuda_toolkit.py`, gated on `IS_WINDOWS`:

- Locates Visual Studio via **`vswhere`** (fixed path under
  `Program Files (x86)\Microsoft Visual Studio\Installer`), then scans MSVC
  toolset directories directly for a real `cl.exe` — the VS default-toolset
  pointer file can point at a version directory that doesn't exist.
- Runs `vcvarsall.bat x64` in a subprocess and imports the environment it
  produces (`PATH`/`INCLUDE`/`LIB`/`LIBPATH`) into the current process, since
  the batch file only mutates its own `cmd.exe`'s environment.
- Adds `-Xcompiler /Zc:preprocessor` to `NVCC_PREPEND_FLAGS` — NVIDIA's CCCL
  headers refuse MSVC's legacy preprocessor without it.
- **`patch_gsplat_msvc_cflags()`** strips gsplat's hardcoded GCC-only
  `-Wno-attributes` flag before it reaches `cl.exe` (upstream gsplat issue
  #809).
- **`patch_gsplat_msvc_source()`** patches two gsplat `.cu` files whose
  `__INS__` explicit-instantiation macros declare trailing params `const`
  in one place but not the other — harmless under GCC's mangling, an
  unresolved-symbol link failure under MSVC's. Both patches live in
  `site-packages` and self-reapply on every `configure()` call, so they
  survive `pip install --upgrade gsplat`.

Verify with `vitrine doctor`. gsplat's JIT cache is keyed on arch/flags, same
as Linux — a `torch`/`gsplat` upgrade forces a ~130 s recompile on first
import after, which is normal.

### 6b. Docker GPU passthrough

**Windows:** works out of the box via Docker Desktop + WSL2 — no equivalent
of the Linux CDI issue below has been seen here.

**Linux:** the CDI spec at `/etc/cdi/nvidia.yaml` pins exact driver library
versions. A partial driver upgrade once left it referencing a stale
`libnvidia-gtk3.so` version, so *every* GPU container failed to start and
COLMAP fell back to CPU (~36 min for 222 images vs ~5 min on GPU). Fixed by
regenerating the spec:

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

`sfm.gpu_available()` always probes and falls back to CPU automatically, so a
stale CDI degrades speed, not correctness, on either OS.

### 7. Multi-camera is a real requirement, not a nicety

The capture mixes multiple phone cameras, a Polycam session, and 720p/4K
video → **several** COLMAP camera entries with different intrinsics *and*
different image dimensions.

Code that reads `cameras[0]` and applies it to every image does not error — it
silently produces a warped reconstruction. `vitrine/colmap_io.py` carries a
per-image camera reference throughout. Keep it that way.

### 8. COLMAP reconstructions have arbitrary scale

Never hard-code a world-unit threshold (densification size, export scale
clamp). Express it as a multiple of `colmap_io.scene_scale(model)`. An absolute
clamp is a bug waiting for a differently-sized room — this bit the export
scale ceiling directly (see `docs/nested-cinema-04-master.md` finding 3).

---

## Source data

`source/` — all captures preserved, nothing deleted. Grown since the original
72-still capture to a five-camera-group set; the full per-group breakdown
(resolutions, roles, per-group PSNR) is in `docs/nested-cinema-04-master.md`.
Folder-level summary:

| Path | Contents | Role |
|---|---|---|
| `source/stills/` | 521 stills across three phone cameras (iPhone 15 Plus HEIC/JPEG, iPhone 14 Pro), full EXIF | **Master.** The archival raw material |
| `source/video/` | `IMG_6318.MOV`, 4K60 HEVC | Fills coverage gaps between stills |
| `source/polycam_frames/` | 383 extracted RGB frames from a Polycam session | Coverage / SfM glue |
| `source/polycam/` | Raw Polycam export bundle (mesh, keyframe pose/motion JSON) | Raw capture record, not trained on directly |
| `source/luma-derivative/` | 383 files, byte-identical to `polycam_frames/` | Luma AI's own keyframes — a derivative. **Do not train on these**, it makes any Luma comparison circular |
| `reference/` | `LumaAI_gs_Test_2.ply`, 1.27M splats, full SH3 | The cloud result to beat. Benchmark only |

`source/` and `reference/` are **git-ignored in full** (not tracked, not
pushed to the public repo) — see "Git and the public repo" below. Nothing
here is deleted or modified locally; it is simply not committed.

`branding/` holds XR Lab / partner logos — leave alone, except the generated
card variants under `branding/acks-*.png` and `branding/vitrine-hero-banner.png`,
which exist specifically to fix light/dark theme contrast in the README (see
git log for how they were built if they need regenerating).

---

## Layout

```text
UOS_Vitrine/
├── AGENTS.md                 ← this file
├── README.md                 ← human setup + usage (public-facing)
├── LICENSE                   ← MIT
├── .gitignore                ← see "Git and the public repo" below
├── docs/
│   ├── PROJECT-PLAN.md       ← roadmap, status, what's next
│   ├── progress.md           ← backward-looking record of what's landed
│   ├── capture-sop.md        ← how to shoot a room so it reconstructs well
│   ├── preservation.md       ← what the archive package contains and why
│   ├── undistortion-finding.md      ← hard-won fact #4, full record
│   ├── nested-cinema-04-master.md   ← hard-won fact #5, full record
│   ├── nested-cinema-03-hq-results.md
│   ├── 5090-port-handoff.md  ← the Linux→Windows port, superseded on root
│   │                            cause by undistortion-finding.md — read that
│   │                            first, this one for the toolchain/port detail
│   └── (internal-only, git-ignored: ai-cop-bid-v3.md, roadmap-ai-cop.md,
│        bid-vs-delivery.md, vitrine-vs-dreamlab.md, ops-parallel-work.md,
│        academic-report.md, *.pptx, *.docx)
├── vitrine/                  ← the package
│   ├── cuda_toolkit.py       ← nvcc + host-compiler discovery, Linux+Windows (§6 above)
│   ├── profiles.py           ← laptop/workstation × draft/standard/archive (§5: archive is unvalidated on workstation)
│   ├── losses.py             ← separable SSIM + photometric objective
│   ├── colmap_io.py          ← COLMAP text-model parsing, multi-camera
│   ├── undistort.py          ← lens-distortion correction (§4 above)
│   ├── dataset.py            ← ViewSet: loads + undistorts + caches views
│   ├── ingest.py             ← frame extraction, sharpness selection
│   ├── sfm.py                ← COLMAP driver, multi-camera aware
│   ├── train.py              ← MCMC + crops + schedules + eval + coverage warning
│   ├── evaluate.py           ← per-camera-group PSNR/SSIM
│   ├── export.py             ← scene.ply → scene.splat, anisotropy/scale/radius guards
│   ├── mesh.py                ← depth fusion → mesh
│   ├── package.py            ← preservation package + manifest
│   ├── serve.py / ui/        ← local dashboard (python -m vitrine ui)
│   └── cli.py                 ← command line entry point
├── scripts/                  ← one-off run/sweep/diagnostic scripts, not the CLI
│   └── run_nested_cinema_04_master.py  ← the validated HQ recipe (§5 above)
├── branding/                  ← logos, hero art (see "Source data" above)
├── source/                    ← input media (git-ignored, see table above)
├── reference/                 ← benchmark artefacts (git-ignored)
├── runs/                      ← outputs, one directory per run (git-ignored)
├── report/                    ← draft academic report (local only, git-ignored)
└── tmp/, output/              ← scratch, build artefacts (git-ignored)
```

---

## Design decisions and their reasons

**Random-crop training.** Source images are held on CPU at 1600–2560 px+;
each step renders a random square window by offsetting the principal point:

```
cx' = cx − x0    cy' = cy − y0    width = height = crop
```

The optimiser sees full-resolution detail while per-step cost stays bounded.
Originally motivated by VRAM; §2 above shows the real benefit is
**throughput**. But the crop-to-source ratio is not a free parameter — see
hard-won fact #5. Keep `crop / source_long_edge` at 0.5 or higher.

**Lens distortion is corrected before training, always.** `ViewSet(...,
undistort=True)` is the default and should stay the default — see hard-won
fact #4. Fisheye models are refused rather than silently mis-corrected; none
of this capture's cameras use one.

**MCMC over the default densification strategy.** `MCMCStrategy` (present in
gsplat 1.5.3) takes a hard `cap_max`, which makes cost and memory deterministic
instead of emergent. Better quality at a fixed splat budget. `cap_max` is
further clamped to `CAP_MAX_POINT_MULTIPLIER × COLMAP point count` in
`train.py`, because a cap sized purely for GPU throughput can vastly exceed
what a given scene has to reconstruct.

**Text COLMAP models.** The pipeline runs `model_converter` to TXT and parses
that. The archival copy is produced as a side effect of normal processing
rather than as a separate export somebody has to remember.

**A splat is a rendering, not evidence.** The preservation package keeps the
originals, the camera poses, software versions, and checksums. That is what
makes the reconstruction reproducible and auditable in ten years.

---

## Environment

### Current — Windows 11, RTX 5090 (primary active machine)

| Fact | Value |
|---|---|
| OS | Windows 11 Home, build 26200 |
| Shell | PowerShell + Git-Bash |
| Python (venv) | 3.11.0, at `.venv/` |
| torch | 2.13.0+cu130, CUDA 13.0 available |
| gsplat | 1.5.3 (`MCMCStrategy` present) |
| GPU | RTX 5090, 32 GB, compute capability 12.0 (sm_120, Blackwell), driver 610.74 |
| Host compiler | MSVC `cl.exe` via VS BuildTools — **not GCC**, see §6-Windows above |
| Docker | 29.1.2, GPU passthrough works out of the box (Docker Desktop + WSL2) |
| COLMAP | not installed natively; `colmap/colmap:latest` in Docker |
| ffmpeg | on PATH |

```bash
.venv/Scripts/python.exe -m vitrine ...
```

### Original dev machine — Linux (CachyOS/Arch), RTX 3060 Laptop

Historical, but still the machine the `LAPTOP_*` profile numbers in
`profiles.py` were measured on.

| Fact | Value |
|---|---|
| OS | CachyOS (Arch), Linux |
| Shell | fish |
| Python (venv) | 3.11, at `.venv/` |
| torch | 2.11+cu128 |
| gsplat | 1.5.3 |
| GPU | RTX 3060 Laptop, 6 GB, compute capability 8.6 |
| CPU / RAM | i7-11800H, 16 threads / 16 GB |
| COLMAP | not packaged for CachyOS; `colmap/colmap:latest` in Docker |
| Docker | `nvidia-container-toolkit` present → GPU containers work (§6b above) |

Python 3.11 rather than the system 3.14 on both machines, for wheel coverage
— notably **Open3D has no Python 3.14 wheel**, which is why `mesh.py` uses
pymeshlab's screened Poisson rather than the Open3D TSDF route most 3DGS
meshing code takes.

---

## Git and the public repo

This repo is now under git and pushed to a **public** GitHub repository:
[github.com/ArtechShadow/UOS-Vitrine](https://github.com/ArtechShadow/UOS-Vitrine)
(MIT licence). Treat anything you add here as potentially public unless it
matches an existing `.gitignore` exclusion.

**Deliberately excluded from git** (present on disk, never committed — check
`.gitignore` before assuming a new file type is covered):

- `runs/`, `source/`, `reference/` — outputs and raw capture media, regenerable
  or personal/institutional, not code
- `tmp/`, `output/`, `report/` — scratch, build artefacts, draft academic report
- `docs/ai-cop-bid-v3.md`, `roadmap-ai-cop.md`, `bid-vs-delivery.md`,
  `vitrine-vs-dreamlab.md`, `ops-parallel-work.md`, `academic-report.md`,
  and any `.pptx`/`.docx` under `docs/` — funding-bid strategy, internal
  partner comparison, task-splits naming people
- `.venv/`, `__pycache__/`, `*.egg-info/`

If you generate something that belongs in one of those categories, keep it
out of `git add` rather than adding an exception — the boundary has been
drawn deliberately (see the `.gitignore` comments for the reasoning per
section).

---

## Related, separate repos

Same family name, different codebases and different licences — **never edit
these from here**, and never merge either into this one:

| Path | What it is |
|---|---|
| `~/Desktop/GitHub/UOS-Vitrine-Capture` | Vitrine Capture — FastAPI + React capture product |
| `~/Desktop/GitHub/Vitrine` / [DreamLab-AI/Vitrine](https://github.com/DreamLab-AI/Vitrine) | DreamLab lab stack — objects, meshing, Unreal Engine 5.8 delivery. **GPL-3.0** — this package is MIT, so nothing from that tree gets vendored or imported here |

This repository is the **preservation pipeline** — archival master and
measured splat. Where the two systems meet, the intended seam is a file-based
handoff (registered images, COLMAP poses, `scene.ply`, checksummed manifest),
not a shared codebase. Same boundary applies to any other GPL-licensed 3DGS
tool considered for this project (e.g. LichtFeld Studio — see
`docs/PROJECT-PLAN.md`'s roadmap section): separate process, never a vendored
import.

---

## Working agreements

1. **Never edit the sibling repos** (`UOS-Vitrine-Capture`, DreamLab `Vitrine`)
   from this project. They are separate products with their own owners, and
   one of them (DreamLab) is GPL-3.0 against this package's MIT.
2. **Never delete anything under `source/` or `reference/`.** All original
   capture files must remain byte-identical.
3. **Measure, don't assume.** Every number in `profiles.py` came from a
   benchmark. If you change a knob, re-run the benchmark and update the
   docstring. A phase that does not improve PSNR *or* SSIM gets reverted, not
   rationalised. `WORKSTATION_ARCHIVE` is the live example of this agreement
   being violated by omission — see hard-won fact #5 before touching it.
4. **Keep the folder clean.** Media in `source/`, outputs in `runs/`, no loose
   files at the root.
5. **Verify against the real data**, never synthetic fixtures.
6. Prefer local, reversible changes; confirm before anything destructive.
7. **Respect the `.gitignore` boundary.** This repo is public now — don't
   `git add -A` without checking `git status` first, and don't add funding,
   partner-strategy, or raw-capture content without deliberately deciding it
   belongs in a public commit.

---

## Quick commands

### Windows (current machine)

```bash
PY=.venv/Scripts/python.exe

# toolchain health
$PY -c "from vitrine import cuda_toolkit; import json; print(json.dumps(cuda_toolkit.status(), indent=2))"

# profile table
$PY -c "from vitrine import profiles; [print(profiles.resolve(q,t)) for t in profiles.TIERS for q in profiles.QUALITY_LEVELS]"

# parse an existing COLMAP model
$PY -c "from vitrine.colmap_io import read_model; print(read_model('<sparse/0>').summary())"

# opacity health check on any result (alive% should be well above single digits)
$PY -c "from plyfile import PlyData; import numpy as np; \
  s=1/(1+np.exp(-np.array(PlyData.read('runs/<name>/model/scene.ply')['vertex']['opacity']))); \
  print('alive', round(float((s>1/255).mean())*100,1),'%')"

# dashboard (live monitoring works during training)
$PY -m vitrine ui --open   # http://127.0.0.1:8765/
```

Confirm gsplat loads from cache (should print in ~0.1 s, not minutes — a
multi-minute pause means it's rebuilding):

```bash
$PY -c "import time,vitrine.cuda_toolkit as c; c.configure(); t=time.time(); import gsplat.cuda._wrapper as w; w._make_lazy_cuda_obj('CameraModelType.PINHOLE'); print(f'{time.time()-t:.1f}s')"
```

### Linux (original dev machine)

```bash
PY=/home/glenn/Desktop/GitHub/UOS-Vitrine-Capture/.venv/bin/python
export VITRINE_GCC_BIN=/home/glenn/Desktop/GitHub/UOS-Vitrine-Capture/.toolchains/gcc15/usr/bin
```

Same commands as above, substituting `$PY`.
