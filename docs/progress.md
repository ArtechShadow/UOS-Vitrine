# Progress report — what has been achieved

Snapshot of everything working, end to end, as of **2026-08-06**. `AGENTS.md`
holds durable facts about the system; `PROJECT-PLAN.md` holds the forward plan;
this document is the backward-looking record of what has actually landed and
been proven against real data. Update it when a milestone lands, don't edit it
to re-plan — that's what `PROJECT-PLAN.md` is for.

---

## 1. The pipeline is built and wired end to end

Every stage of `ingest → sfm → train → evaluate → export → package/mesh` has a
real implementation in `vitrine/`, and all of them are reachable through one
CLI (`vitrine/cli.py`):

```
vitrine doctor      # checks the machine can run the pipeline
vitrine profiles    # shows the hardware-profile table
vitrine ingest      # classify + extract + select source frames
vitrine sfm         # solve camera poses with COLMAP
vitrine train       # train the Gaussian splat (MCMC + SSIM + eval)
vitrine evaluate     # measure a trained splat, per camera group
vitrine package      # assemble the preservation package + manifest
vitrine run          # ingest + sfm + train + package, one shot
vitrine verify       # re-check a package's checksums against its manifest
```

| Module | What it does | State |
|---|---|---|
| `cuda_toolkit.py` | Finds a working `nvcc`/`gcc` pair, patches the CUDA runtime symlinks and preload order | done, documented in `AGENTS.md` §4 |
| `losses.py` | `0.8·L1 + 0.2·(1−SSIM)`, separable SSIM in pure PyTorch | done, benchmarked (9–17% overhead) |
| `profiles.py` | 6 hardware profiles (laptop/workstation × draft/standard/archive) | done, all numbers measured on the 3060 |
| `colmap_io.py` | Reads COLMAP's text sparse model, multi-camera aware | done |
| `ingest.py` | Classifies sources by resolution/EXIF, extracts video frames, scores sharpness, selects an even-coverage subset | done, proven on real capture |
| `sfm.py` | Drives COLMAP in Docker, exhaustive matching, EXIF-preserving resize | done, proven on real capture |
| `train.py` | MCMC densification, random-crop rendering, SH warm-up, LR decay, held-out eval | done, proven on real capture |
| `evaluate.py` | Per-camera-group PSNR/SSIM on a trained splat | done |
| `export.py` | `scene.ply` → `scene.splat` (compact browser format) + preview renders | done, proven on real capture |
| `package.py` | Assembles `originals/ + sfm/ + model/ + derivatives/ + manifest.json` with SHA-256 checksums | done, **not yet run** on the Nested Cinema capture |
| `mesh.py` | Depth fusion (via `pymeshlab`, Open3D worked around) → textured mesh | done, **not yet run** on the Nested Cinema capture |
| Project venv | Own `uv`-managed Python 3.11 venv at `.venv/`, no longer borrowing the sibling product's interpreter | done |

This is materially ahead of `PROJECT-PLAN.md`'s status table, which predates
this work and still marks `sfm`/`train`/`package`/`mesh`/`cli` as "todo" — see
§5 below.

---

## 2. One full real capture has gone through the whole pipeline

**Run:** `runs/nested-cinema-01` — the *Nested Cinema* installation room-set
(newspaper-clad walls, sofa, reel-to-reel deck, filing cabinet, rug), captured
as 72 iPhone 14 Pro stills + one 720p video pass.

| Stage | Result | Time |
|---|---|---|
| **Ingest** | 2 camera groups staged — 72 stills (4344×5792) + 150 video frames (1280×720, sharpness-selected from ~243 candidates, bottom 15% by Laplacian variance rejected) | — |
| **SfM** (COLMAP) | 222/222 images registered, 2 camera models, 111,203 sparse points | 21:17–21:53, **36 min on CPU** — this run predates the CDI GPU-passthrough fix in `AGENTS.md` §4b; a re-run today would take ~5 min on GPU |
| **Train** (`laptop-standard`, 15,000 iterations) | see §3 below | 22:00–23:01, **60.4 min**, peak VRAM 1.94 GB |
| **Export** | `scene.ply` (248 MB, full SH) → `scene.splat` (17.8 MB, compact) | 23:01–23:03 |

Total wall clock for a from-scratch capture to a viewable splat: **~1.5 hours**
on a 6 GB RTX 3060 Laptop, comfortably inside the 6 GB VRAM budget (peak
measured at under 2 GB).

---

## 3. Training result — quality achieved

`laptop-standard` profile completed its full **15,000/15,000 iterations**
(this was a point of confusion mid-session: the retained `checkpoint_10000.ply`
looked like a stopping point, but it's just a periodic checkpoint — the run
continued to completion and `scene.ply`/`train.json` are the final output).

| Step | PSNR | SSIM | Gaussians |
|---|---|---|---|
| 2,500 | 16.42 dB | 0.550 | 295,041 |
| 5,000 | 16.45 dB | 0.557 | 999,097 |
| 7,500 | 17.34 dB | 0.565 | 1,000,000 |
| 10,000 | 17.33 dB | 0.571 | 1,000,000 |
| 12,500 | 18.91 dB | 0.586 | 1,000,000 |
| **15,000 (final, held-out)** | **25.72 dB** | **0.817** | 1,000,000 |

The jump from step 12,500 to the final held-out eval is expected: MCMC
densification stops at step 11,250 (75% of the run), and the remaining steps
are pure photometric refinement on a now-fixed Gaussian population.

Scene scale 7.449 world units; 22,562 Gaussians (2.3%) were scale-clamped on
export to keep outliers from blowing up the bounding volume.

**Not yet done:** the project's own verification plan (`PROJECT-PLAN.md`
§Verification) calls for a side-by-side against `reference/LumaAI_gs_Test_2.ply`
and a 1:1 newspaper-clipping legibility check. Neither has been formally run
against this result yet.

---

## 4. This session — getting the result actually viewable

The trained splat existed but nothing had rendered it. This session built the
viewing path and fixed three real problems along the way:

1. **Local web viewers**, self-hosted (no upload of the capture anywhere):
   - `scene.splat` in the standard [antimatter15/splat](https://github.com/antimatter15/splat)
     WebGL viewer — the compact, band-0-SH derivative.
   - `scene.ply` in a Three.js/[GaussianSplats3D](https://github.com/mkkellogg/GaussianSplats3D)
     viewer — the full-SH 248 MB master, for comparison. Visibly sharper
     colour and better off-angle shading than the `.splat` derivative, as
     expected from `export.py`'s own documented trade-off.
2. **GPU rendering fix.** The viewer was getting 15 fps. Root cause: this is
   an Optimus laptop (Intel iGPU + RTX 3060 Mobile), and the browser was
   rendering WebGL on the Intel iGPU while the 3060 sat idle (`nvidia-smi`
   showed 25% util / 127 MB during viewing). Relaunching Chromium in an
   isolated profile with `__NV_PRIME_RENDER_OFFLOAD=1` /
   `__GLX_VENDOR_LIBRARY_NAME=nvidia` forced it onto the dedicated GPU:
   **15 fps → 94 fps**, confirmed by GPU utilization jumping to 83% and VRAM
   usage roughly tripling.
3. **Orientation fix.** The scene initially rendered visibly tilted — the
   viewer's `cameraUp` had been guessed. Recomputed the true up vector
   directly from the 222 COLMAP camera poses in
   `sfm/sparse_text/images.txt` (average of each frame's image-up direction
   mapped into world space via `R.T @ [0,-1,0]`, per `colmap_io.py`'s own
   rotation convention): `[0.1105, -0.9937, 0.0168]`, not the arbitrary
   `[0, -1, -0.2]` used at first. The room now renders level.

None of this touched the archival files — it's all local server + viewer
config in a scratch directory, not a repo change.

---

## 5. Status table correction

`PROJECT-PLAN.md`'s status table (as of 2026-08-05) undercounts what's done —
it was last updated before `sfm.py`, `train.py`, `package.py`, `mesh.py`, and
`cli.py` landed. That table should be refreshed to match this document; see
`PROJECT-PLAN.md` for the corrected version.

---

## 6. What's genuinely still open

- **Packaging.** `package.py` is implemented but has not been run against
  `runs/nested-cinema-01` — there is no `archive/` folder with a manifest and
  checksums yet. This is the actual preservation deliverable; the splat alone
  is not.
- **Mesh export.** `mesh.py` is implemented (via `pymeshlab`, working around
  the Open3D/Python 3.14 blocker) but has not been run for this capture.
- **Formal quality gates.** Luma comparison and the 1:1 newspaper-legibility
  check from the verification plan haven't been done yet.
- **`sudo pacman -S gcc15`** — still a manual step; the `VITRINE_GCC_BIN`
  env var stopgap is required until then (`AGENTS.md` §4).
- **Screens-in-the-installation decision** — unresolved interpretive question
  about whether to capture with screens off/on-a-held-frame/masked (`PROJECT-PLAN.md`
  §Open questions).
