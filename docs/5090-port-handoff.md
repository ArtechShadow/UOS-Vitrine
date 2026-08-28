# Vitrine — RTX 5090 / Windows port & quality investigation — HANDOFF

**Written 2026-08-06.** Handoff for whoever (human or AI) continues this work.
This captures the port from the original CachyOS/RTX 3060-Laptop dev box to
this Windows 11 / RTX 5090 workstation, plus an unfinished investigation into
a training-quality regression. Read `AGENTS.md` first for the project's own
rationale; this doc is the delta on top of it.

---

## 1. TL;DR / current status

- **Environment port: DONE and verified.** The full pipeline runs end-to-end
  on Windows + RTX 5090 (ingest → SfM → train → package → UI/viewer).
- **Speed: confirmed huge.** The proven 3060 recipe reproduces its exact
  quality here in **2.4 min vs 60.4 min** (~25×).
- **Quality "beyond the 3060": NOT achieved, and the reason is now known.**
  The `workstation-archive` profile's `crop=1600 / source_long_edge=4096`
  causes a catastrophic Gaussian **opacity collapse** (99.8% of splats die),
  giving a sparse star-field render and ~14–17 dB PSNR. Small crops (768) are
  healthy. **Root cause isolated — see §4.** Fix designed but not implemented.
- **Decision pending from the user:** whether to (a) lock in the 25 dB control
  result as "the 5090 run", (b) fix the profile to genuinely exceed the 3060,
  or (c) both. User's last instruction was to hand this off.

---

## 2. Machine / environment facts (this PC)

| Fact | Value |
|---|---|
| OS | Windows 11 Home 26200, PowerShell + Git-Bash |
| GPU | RTX 5090, 32 GB, compute capability **12.0 (sm_120, Blackwell)**, driver 610.74 / CUDA 13.3 |
| CPU / RAM | 16 logical cores / 32 GB |
| Python | `C:\Users\realg\AppData\Local\Programs\Python\Python311\python.exe` (3.11) |
| venv | `C:\Users\realg\Desktop\UOS_Vitrine\.venv` (rebuilt native-Windows; the copied Linux one was deleted) |
| torch | **2.13.0+cu130** (cu124 in old requirements predates Blackwell) |
| gsplat | 1.5.3, CUDA extension JIT-compiled & cached at `%LOCALAPPDATA%\torch_extensions\...\py311_cu130\gsplat_cuda` |
| Host compiler | **MSVC** `cl.exe` 14.50.35717 from VS 2026 BuildTools (`C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`) — NOT GCC |
| COLMAP | Docker `colmap/colmap:latest`, GPU passthrough works out-of-the-box via Docker Desktop/WSL2 |
| ffmpeg | on PATH (winget Gyan build) |

`python -m vitrine doctor` → all green (CUDA, MSVC, gsplat prebuilt, docker GPU, ffmpeg).

**There is no git repo here.** Consider `git init` before further changes so
this session's edits are recoverable.

---

## 3. Code changes made this session (all in `vitrine/`)

### `cuda_toolkit.py` — Linux→Windows port (biggest change)
- Branches on `IS_WINDOWS`. Linux path (GCC/`.so`/`LD_LIBRARY_PATH`/RTLD) is
  untouched; a parallel Windows path was added.
- Windows: finds `nvcc.exe` in the pip wheel, locates VS via **vswhere**,
  scans MSVC toolset dirs directly for a real `cl.exe` (the VS "18" default
  toolset pointer was broken — pointed at a version dir that didn't exist),
  runs `vcvarsall.bat` and imports its env (`PATH/INCLUDE/LIB/LIBPATH`).
- Adds `-Xcompiler /Zc:preprocessor` to `NVCC_PREPEND_FLAGS` (NVIDIA CCCL
  headers refuse MSVC's legacy preprocessor).
- **`patch_gsplat_msvc_cflags()`** — strips gsplat's hardcoded GCC-only
  `-Wno-attributes` flag before it reaches `cl.exe` (upstream bug, gsplat
  issue #809).
- **`patch_gsplat_msvc_source()`** — patches two gsplat `.cu` files
  (`RasterizeToPixels2DGSBwd.cu`, `RasterizeToPixelsFromWorld3DGSFwd.cu`)
  whose `__INS__` explicit-instantiation macros declare trailing params
  `const` while the header/definition don't. Harmless on GCC (identical
  mangling) but MSVC mangles them differently → unresolved-symbol link
  failure. This patch lives in `site-packages`, so it self-reapplies on
  every `configure()` (survives `pip install --upgrade gsplat`).

### `requirements.txt`
- `--extra-index-url` bumped `cu124` → **`cu130`**; added `packaging` (gsplat
  imports it but doesn't declare it).

### `sfm.py`
- `os.getuid()`/`os.getgid()` (Linux-only, crashes on Windows) now guarded
  with `hasattr(os, "getuid")` — Docker Desktop maps bind-mount ownership
  itself, so `--user` is simply skipped on Windows.

### `cli.py`
- `main()` reconfigures stdout/stderr to UTF-8 (Windows cp1252 couldn't
  encode the `→`/`—` the CLI prints — was crashing `ui`, mojibaking `doctor`).
- File logging added: pipeline stages now write `<run-dir>/logs/vitrine.log`
  (the UI log-tail feature existed but nothing ever wrote the file).

### `serve.py` + `ui/app.js` + `ui/styles.css` — live monitoring
- Training now writes `<run-dir>/model/progress.json` every ~500 steps
  (step, loss, ssim, gaussian count, eta, energy). Deleted when `train.json`
  (the finished report) lands.
- UI polls it every 4 s: live "Training now" banner, pulsing stage row,
  auto-refreshing metric cards + log tail, "● training" badge in the list.
- **Note:** the UI (`app.js`, `styles.css`, `index.html`, `brand/*`) was also
  substantially restyled/rewritten by the user/another tool mid-session
  (academic/heritage theme, plain-language labels, advanced-mode toggle).
  Treat the current UI files as the source of truth; don't revert.

### `train.py` — energy tracking + the quality-fix attempts
- **Energy/cost:** samples `nvidia-smi` power draw every 500 steps, integrates
  to kWh, ×`VITRINE_ELECTRICITY_RATE_GBP` (default 0.2611 £/kWh UK average). In
  `progress.json`, `train.json`, and a new UI "Electricity cost" card.
- **Opacity instrumentation:** the step log line now prints `mean_op` and
  `alive %` (fraction of Gaussians with sigmoid(opacity) > 0.005). **This is
  the diagnostic that cracked the case — keep it.**
- **`CAP_MAX_POINT_MULTIPLIER = 15`** — clamps `cap_max` to 15× the COLMAP
  point count. (Was a real over-provisioning issue but a RED HERRING for the
  collapse; see §4. Harmless, can stay.)
- **`LR_DECAY_HORIZON_STEPS = 15_000`** — position-LR decay is now over a
  FIXED horizon instead of `profile.iterations`. (Genuine latent bug: a
  30k-iter run previously decayed at half the rate of the validated 15k run.
  Correct fix, but also NOT the cause of the collapse.)
- **Refine window** now timed off `min(profile.iterations, LR_DECAY_HORIZON_STEPS)`
  to stay coupled to the LR decay above.

### `profiles.py`
- `workstation` tier `relative_throughput` 7.0 → **32.0** (measured on the
  5090, see below). Docstrings updated with the measurements.

---

## 4. THE QUALITY INVESTIGATION — root cause (hard-won, do not re-derive)

**Symptom:** `workstation-archive` produces a sparse "star-field" splat —
99%+ of Gaussians collapse to ~zero opacity and get filtered out at export
(`scene.splat` ~0.07 MB vs a healthy ~17 MB), PSNR ~14–17 dB vs the 3060's
25.7 dB.

**The controlled experiment matrix (all on THIS PC, current code):**

| Run dir | crop / source | cap | alive Gaussians | PSNR | time |
|---|---|---|---|---|---|
| `nested-cinema-01` (3060 baseline) | 768 / 2048 | 1.0M | ~555k (55%) | 25.72 | 60.4 min |
| **`nested-cinema-01-5090-control`** | **768 / 2048** | 1.0M | **528k (53%)** | **25.06** | **2.4 min** |
| `nested-cinema-01-5090-safecap` | 1600 / 4096 | 1.0M | 2.2k (0.2%) | 17.49 | 7.6 min |
| `-uncapped-cap-bug` (backup) | 1600 / 4096 | 6.0M | ~80k (1.3%) | 14.31 | 23.6 min |

**Conclusion:** the collapse tracks **`crop` / `source_long_edge`, NOT
`cap_max` and NOT the LR schedule.** Every healthy run used crop=768; every
collapsed run used crop=1600. The `cap_max` and LR fixes in §3 addressed real
but *separate* latent bugs — they moved the number a little but never fixed
the collapse, because the big crop was the actual driver the whole time.

**Mechanism (hypothesis, well-supported by the `alive %` trace):** the trainer
renders ONE random crop per step (`dataset.ViewSet.crop`). A 1600px window of
a 4096px source means any given Gaussian is in-frame far less often per unit
training, while `OPACITY_REG`/`SCALE_REG` pull on *every* Gaussian *every*
step. With big crops, regularisation-to-death outruns the reconstruction
gradient that keeps a Gaussian alive. The `alive %` trace shows a clear runaway:
healthy 90–99% until the population grows toward the cap, then 83%→27%→1.6%
over ~5k steps, accelerating (MCMC relocates dead splats by cloning live ones,
so a shrinking live pool feeds its own collapse). Small crops keep everything
in frame often enough to survive.

**The `workstation-archive` profile as written (crop 1600 / source 4096) is
therefore unvalidated and actively harmful.** This is exactly the situation
`AGENTS.md` working-agreement #3 is about ("A phase that does not improve PSNR
*or* SSIM gets reverted, not rationalised").

---

## 5. Recommended path forward (not yet done)

To **match** the 3060 on this PC right now: just use `--quality standard
--tier laptop` (the control config). 25 dB, 2.4 min, healthy. Done.

Before capturing more material, run a **stills-only baseline** from the existing
72 archival masters. A same-camera held-out comparison shows that the source
still contains sharp sofa, newspaper and equipment detail that both the 5090
control and Q1 render softly. That pattern points first to mixed-source
optimisation and pose/intrinsic consistency, not an insufficient total image
count. Reintroduce video only for verified coverage gaps. Capture more only for
surfaces absent from the still set, using sharp close-range photographs with
strong overlap and stable focus/exposure; more 720p video is likely to increase
softness.

To genuinely **exceed** the 3060 (the actual ask), the promising direction —
**needs experimentation, budget a few 3–8 min runs**:

1. **Raise crop/source AND cap together AND cut `OPACITY_REG`.** The three are
   coupled. Bigger crops need more splats to fill the extra detail, and the
   per-Gaussian regularisation pressure must drop so survivors aren't killed
   faster than a sparsely-sampled big crop can rescue them. Try e.g.
   crop=1024, source=3072, cap≈1.5M, `OPACITY_REG` 0.01→0.002. Watch the
   `alive %` trace — success = it stays >40% through training.
2. **Or** render multiple crops per step (mini-batch) so every Gaussian is
   seen more often — a bigger `dataset.py`/`train.py` change but attacks the
   mechanism head-on.
3. **Then** re-tune `profiles.py::WORKSTATION_*` to whatever actually holds up,
   and re-measure — don't ship the current crop=1600 numbers.

Also consider raising SfM quality (`colmap_long_edge`) and running COLMAP
denser — more/better points may support more Gaussians cleanly.

---

## 6. Run-directory inventory (as of handoff)

- `nested-cinema-01` — **the 3060 baseline. DO NOT MODIFY.** 25.72 dB reference.
- `nested-cinema-01-5090` — the "main" 5090 dir. Currently holds the
  **17.5 dB collapsed** archive result (its `model/` was overwritten from
  `-safecap`). Its ingest/SfM outputs are good and reused by the scripts below.
- `nested-cinema-01-5090-control` — **the good one: 25.06 dB, healthy.** This
  is the strongest "5090 run" to promote if locking in a result.
- `nested-cinema-01-5090-safecap` — 17.5 dB, crop=1600 @ cap 1M (collapsed).
- `nested-cinema-01-5090-crop768` — an earlier partial/confounded test; can delete.
- `nested-cinema-01-5090-uncapped-cap-bug` — 7.4 GB backup of the first broken
  6M-splat run, kept as visual evidence. Safe to delete to reclaim space.

**If locking in the control result:** copy `nested-cinema-01-5090-control/model`
over `nested-cinema-01-5090/model`, regenerate `scene.splat` (see §7), then
`python -m vitrine --run-dir runs/nested-cinema-01-5090 package --title ... --subject ...`.

---

## 7. Reproduction / useful commands

```bash
cd /c/Users/realg/Desktop/UOS_Vitrine
PY=.venv/Scripts/python.exe

# health
$PY -m vitrine doctor
$PY -m vitrine profiles          # note workstation ~min are now optimistic (throughput 32x)

# the GOOD control config (matches 3060, 2.4 min) — reuses existing ingest/sfm:
#   (laptop-standard = crop768/source2048/cap1M/15k iters)
# via CLI needs a run dir WITH sfm already; the main 5090 dir has it:
$PY -m vitrine -v --run-dir runs/nested-cinema-01-5090 --quality standard --tier laptop train

# regenerate the web-viewer splat from a scene.ply
$PY -c "from vitrine.export import write_splat_file; from pathlib import Path; \
        write_splat_file(Path('runs/<name>/model/scene.ply'), Path('runs/<name>/model/scene.splat'))"

# opacity health check on any result (alive% should be ~50%, not <1%)
$PY -c "from plyfile import PlyData; import numpy as np; \
  s=1/(1+np.exp(-np.array(PlyData.read('runs/<name>/model/scene.ply')['vertex']['opacity']))); \
  print('alive', round(float((s>1/255).mean())*100,1),'%')"

# dashboard (live monitoring works during training)
$PY -m vitrine ui            # http://127.0.0.1:8765/

# electricity rate override
export VITRINE_ELECTRICITY_RATE_GBP=0.24
```

Full-pipeline run from scratch (note: **must** pass `--include stills video`
to exclude the derivative Luma frames, which `AGENTS.md` forbids training on;
global flags go BEFORE the subcommand):

```bash
$PY -m vitrine -v --run-dir runs/<name> --quality standard --tier laptop run \
    --include stills video --title "..." --subject "..."
```

---

## 8. Loose ends / gotchas

- Two throwaway scratch scripts drove the reused-ingest experiments; they live
  in the session scratchpad, not the repo. The pattern (load model, `replace()`
  the profile, call `train.train()`) is trivial to recreate.
- `profiles.py` workstation `~min` estimates are now optimistic because
  `estimated_minutes()` divides by throughput 32 but the crop=1600 profiles
  don't converge anyway — moot until the profile is re-tuned.
- The diagnostic scripts bypassed `_setup_logging`, so their `logger.info`
  lines (incl. the `alive %`) don't print; watch `model/progress.json` instead,
  or run through `python -m vitrine ... train` which sets up logging.
- gsplat's JIT cache is keyed on arch/flags; if `torch`/`gsplat` are upgraded,
  first import recompiles (~130 s) — this is normal, not a hang.
