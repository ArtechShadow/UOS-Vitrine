# Project plan and status

Living document. **`AGENTS.md` holds the facts; this file holds the plan.**
Update the status table whenever a module lands.

Last updated: 2026-08-06

---

## Goal

Produce **archive-grade 3D Gaussian Splats** from video and photographs of the
*Nested Cinema* installation — free, fully local, reproducible, running on a
6 GB RTX 3060 Laptop today and on RTX 5090-class hardware later.

Requirements agreed with Glenn Watts (University of Salford, SAMCT):

| Requirement | Meaning |
|---|---|
| Dual profile | One pipeline, laptop and workstation presets |
| Stills + video | 72 × 25 MP stills as master, video frames fill gaps |
| Preservation package | Originals + poses + versions + checksums, reproducible |
| Web-viewable splat | Compressed derivative for access |
| Mesh + texture | Conventional geometry for repository deposit |
| Legible clipping detail | Newspaper text on the walls must be readable |
| Reusable pipeline | Documented SOP, not a one-off |

Deferred, explicitly: object segmentation (SAM3D → per-object meshes → Meshy).
That belongs to the DreamLab `Vitrine` stack.

---

## Why this exists

Glenn's existing cloud result — `reference/LumaAI_gs_Test_2.ply`, 1.27M splats
— is the quality bar to beat, and it is a cloud service. The sibling product
`UOS-Vitrine-Capture` already runs a local COLMAP → gsplat path but its output
is not archive-grade. Reading that code identified specific, fixable causes,
and this project rebuilds the reconstruction core around them.

The diagnosis (verified by reading `UOS-Vitrine-Capture/backend/`, not
inferred) — kept here because it is the rationale for every design choice:

| Problem found | Consequence | Addressed by |
|---|---|---|
| Loss is L1+MSE, **no SSIM** | Blur is invisible to the objective | `losses.py` |
| `DefaultStrategy`, not MCMC | Splat count emergent, not bounded | `train.py` |
| Constant LR, no SH warm-up | Geometry never settles | `train.py` |
| Scale clamp in **absolute world units** | Clips walls/floor at room scale | `colmap_io.scene_scale` |
| Single-camera assumed in SfM *and* trainer | Silently warps mixed stills+video | `colmap_io.py`, `sfm.py` |
| EXIF stripped before COLMAP | Loses the iPhone focal prior | `sfm.py` |
| Sequential matcher above 48 images | Weak loop closure in an enclosed room | `sfm.py` |
| Trained at 960 px from 5792 px source | ~36× reduction; text unreadable | `profiles.py`, `train.py` |
| No held-out eval | "HQ" unmeasurable | `train.py` |

---

## Status

| # | Module | State | Notes |
|---|---|---|---|
| 1 | `cuda_toolkit.py` | **done** | nvcc + gcc discovery; cache-invalidation trap documented in AGENTS.md §4 |
| 2 | `losses.py` | **done** | Separable SSIM, validated + benchmarked (9–17% overhead) |
| 3 | `profiles.py` | **done** | 6 profiles, all numbers measured on the 3060 |
| 4 | `colmap_io.py` | **done** | Multi-camera; tested against 3 real models |
| 5 | `ingest.py` | **done** | Proven end to end on Nested Cinema: 222/315 frames accepted after sharpness selection |
| 6 | `sfm.py` | **done** | Proven end to end: 222/222 images registered, 111,203 points |
| 7 | `train.py` | **done** | Proven end to end: 15,000/15,000 iters, 25.72 dB PSNR / 0.817 SSIM held-out |
| 8 | `evaluate.py` | **done** | Per-camera-group PSNR/SSIM |
| 9 | `export.py` | **done** | `scene.ply` → `scene.splat`, proven on Nested Cinema |
| 10 | `package.py` | **implemented, not yet run** | Manifest + checksums coded; no `archive/` output produced yet for Nested Cinema |
| 11 | `mesh.py` | **implemented, not yet run** | Depth fusion via `pymeshlab` (Open3D py3.14 blocker routed around); not yet run for Nested Cinema |
| 12 | `cli.py` | **done** | `vitrine doctor\|profiles\|ingest\|sfm\|train\|evaluate\|package\|verify\|run` all wired |
| 13 | Docs | partial | AGENTS.md, capture-sop.md, preservation.md, `progress.md` done; nothing else queued |
| 14 | Project venv | **done** | Own `uv`-managed Python 3.11 venv at `.venv/`, no longer borrowing the sibling product's interpreter |

Full detail and real-run numbers: `docs/progress.md`.

---

## Remaining work

### 5. `ingest.py` — next up

Classify `source/` by resolution and EXIF into camera groups; extract video
frames with ffmpeg; score sharpness (OpenCV Laplacian variance); select the
sharpest frame per temporal bucket so coverage stays even.

Keep stills and video frames in **separate output folders** — `sfm.py` relies
on that to give COLMAP one camera model per folder.

Low light is expected (a cinema installation), so motion blur will be common in
the video. Sharpness selection matters more here than in a typical capture.

### 6. `sfm.py`

Drive COLMAP via Docker (`colmap/colmap:latest`; not packaged for CachyOS).
Must:

- use `--ImageReader.single_camera_per_folder 1`, **not** `single_camera`
- preserve EXIF when resizing (`im.save(..., exif=im.info.get("exif"))`) so
  COLMAP keeps the iPhone focal prior
- `--ImageReader.camera_model OPENCV`
- exhaustive matching for the 72 stills (~2.5k pairs is minutes, and loop
  closure matters in an enclosed room); sequential for video frames
- a second `bundle_adjuster` pass with `--BundleAdjustment.refine_principal_point 1`
- run `model_converter --output_type TXT` — feeds both `colmap_io` and the
  preservation package

Known risk: the mirror in the corner of the set. Reflections generate false
correspondences. Mask if registration is poor.

### 7. `train.py` — the core

- `MCMCStrategy(cap_max=profile.cap_max)`
- random-crop rendering (see AGENTS.md, "Design decisions")
- exponential LR decay on `means` (~100× over the run)
- SH degree warm-up, one band per 1000 steps
- scale clamp as a multiple of `scene_scale`
- bilateral grid for per-image exposure/white-balance drift — 72 handheld
  frames in a dim room *will* drift; port `lib_bilagrid.py` from the gsplat
  examples (MIT)
- hold out every 8th image; report PSNR/SSIM/LPIPS

### 8. `package.py`

```
runs/<run-id>/archive/
├── originals/      untouched source, original filenames
├── sfm/            cameras.txt · images.txt · points3D.txt · database.db
├── model/          scene.ply (full SH)
├── derivatives/    scene.sog (web) · mesh.glb · preview renders
├── manifest.json   software versions, git SHAs, profile, metrics, SHA-256 of every file
└── README.md       human-readable, needs no tooling to understand
```

### 9. `mesh.py`

Render depth at training poses (`render_mode="RGB+ED"`, confirmed present),
TSDF-fuse, extract, export glTF.

**Blocked:** Open3D has no Python 3.14 wheel. Options — dump depth+pose and
fuse in a side venv on an older Python; or use `gsplat.rasterization_2dgs`
(confirmed present) with Poisson via `pymeshlab`. Decide when reached; do not
let it block 5–8.

---

## Verification plan

Run against the real data throughout. No synthetic fixtures.

1. **Baseline** — record PSNR/SSIM/splat count/VRAM/wall time on the first
   complete run, before tuning.
2. **Per change** — same input, same held-out split, same metrics. Anything
   that does not improve PSNR *or* SSIM gets reverted.
3. **Mixed-source gate** — 72 stills + video frames must register in one model
   with **two** entries in `cameras.txt`.
4. **Legibility gate** — crop a newspaper clipping from a held-out render at
   1:1 and compare with the source JPEG. Text should be readable. This is the
   requirement the old 960 px path could never meet.
5. **VRAM gate** — peak stays well under 6 GB (measurements suggest ~1.5 GB, so
   this should be comfortable).
6. **Beat Luma** — side-by-side against `reference/LumaAI_gs_Test_2.ply`.
7. **Package validates** — checksums verify, manifest complete.

---

## Open questions

- **`sudo pacman -S gcc15`?** Needs Glenn. Until then the stopgap
  `VITRINE_GCC_BIN` env var is required for any GPU work. See AGENTS.md §4.
- **Screens in the installation.** *Nested Cinema* includes playing screens. A
  moving image is both view-dependent and time-varying; 3DGS will smear it or
  bake in one arbitrary frame. Decide whether to capture with screens off, on
  a held frame, or mask them — and record the choice in the manifest, because
  it is an interpretive decision about what is being preserved.
- **Mesh path** once Open3D is ruled in or out.

---

## Possible future directions

Unvalidated ideas, not commitments — each would need the usual measure-first
treatment (`AGENTS.md` working agreement #3) before touching a production
profile.

- **[QuerySplat](https://inspatio.github.io/querysplat/)** — a feed-forward,
  pose-free 3D Gaussian Splatting method (dual-branch geometry/appearance
  decoder, SOTA on DL3DV). Relevant because it could bypass or shortcut the
  COLMAP SfM stage entirely for a draft-quality pass — worth a speed/quality
  comparison against `sfm.py` + `train.py` on Nested Cinema before any
  pipeline change.
- **[LiteReality-Agent](https://litereality.github.io/Litereality-agent-site/)**
  — converts iPhone LiDAR scans into editable, code-based scene programs
  (`Room.py`) rather than raw meshes or splats, with an agentic refine loop.
  Different technique and a different deliverable (interactive/editable scene
  vs. an archival splat), but relevant to the deferred object/interactive
  path — closer to DreamLab's territory (object segmentation, engine
  delivery) than this repo's archival-splat scope.
- **[LichtFeld Studio](https://github.com/MrNeRF/LichtFeld-Studio)** — a
  native C++ application to train, inspect, edit, and export 3DGS scenes;
  this is the trainer DreamLab Vitrine already vendors downstream of this
  pipeline. Worth watching as a potential faster/more capable alternative to
  the `gsplat`-based `train.py` — but it is **GPL-3.0**, and this package is
  MIT, so it could only ever be integrated as a separate process (CLI/file
  handoff), never imported or vendored directly. Same licence boundary noted
  for the DreamLab integration generally.
