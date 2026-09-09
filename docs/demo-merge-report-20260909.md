# Demo integration merge report — 9 September 2026

Prepared before updating main. The user requested a ten-minute merge deadline
during final validation; remaining rehearsal limits below are explicit.

## Commits and rollback

- Original main: `03ac9ba6570097a1ff807077ebed9321aea99d5f`.
- Rollback branch: `backup/pre-demo-main-2026-09-09`.
- Demo commits: `83f364608270327e39f7b5a8d0df56091896bca5` and
  `ccc36bd8eb0cbb52db720be8f39cb83d16597063`.
- Integration branch: `integration/demo-merge`, created from main and populated
  with both demo commits. Main is their ancestor: no text conflicts or conflict
  resolutions were necessary. A further integration-fix commit accompanies them.
- Main is updated with a normal fast-forward only; no force push or history rewrite.

## Resulting changes

- Generic GPU/CPU/RAM discovery, validated manual configuration and worker/cache
  budgets. The exact previously measured 2304/1536, 2M-cap, 15k-step demo recipe
  is named and selected by default. Legacy numeric profiles remain available
  with low-coverage warnings. 5090-derived ETA is not assigned to an A6000.
- Adaptive video selection scores sharpness, exposure and duplicates while
  preserving temporal coverage. Explicit integer video budgets retain legacy
  reproducibility. Full-duration sampling replaces the adaptive path's old
  first-600-frame window, with an ingest-wide safety cap. Camera EXIF grouping,
  same-stem videos and output preservation are corrected.
- Mandatory full-run preflight, stage timing, persisted configuration/state,
  exclusive run lock, cooperative cancellation, verified-stage resume and
  explicit master-to-viewer export. Heavy CUDA extension readiness is checked
  before expensive processing; stopped Docker produces a visible failure.
- Existing gsplat objective, undistortion, multi-camera geometry, CUDA compiler
  setup, MCMC and main's quality guards remain. Cleanup writes a separate
  candidate with provenance; it is not promoted automatically.
- Existing segmentation sidecar remains separate. Heavy local mesh engines use
  an optional image/mask manifest-to-GLB process interface. Existing experimental
  Poisson meshes additionally export embedded GLB without a new dependency.
- Packaging verifies a fresh archive before publication and preserves previous
  generations. Invalid optional mesh archival emits a warning rather than
  invalidating scene reconstruction.
- Bounded spooled multipart upload, preserved session staging, optional launcher
  sidecar, accurate pipeline API state and basic resume/cancel/output-folder UI.
- iOS source remains intact and optional; dashboard import remains disabled.

Substantially extended/new files: `hardware.py`, `profiles.py`, `dataset.py`,
`ingest.py`, `frame_selection.py`, `cleanup.py`, `preflight.py`, `pipeline.py`,
`telemetry.py`, `engines.py`, `mesh_glb.py`, `serve.py`. Trainer/SfM changes wrap
the existing work with timing, cancellation and resource configuration.
See the consolidated audit table in `demo-integration-plan.md` for all categories.

## Dependencies and portability

No core ML dependency was added or upgraded. The incoming branch's optional
desktop `pywebview==6.2.1` and mesh `pymeshlab` requirements remain separate.
`config/constraints-windows-cu130.txt` records the working installed versions.
Pytest/Ruff were installed only in ignored integration scratch storage.
No model weights, raw media, reconstructions, credentials or machine-local
configuration are committed. Active core code uses discovered paths and
configuration; historical diagnostic scripts/docs still include recorded
machine paths and are not a claim of a completely path-free repository.

## Validation completed before merge

- Full available pytest suite: **155 passed, 15 skipped**, zero failures.
- Focused Ruff syntax/undefined-name checks passed; `git diff --check` passed.
- Actual RTX 5090 / compute 12.0 / CUDA 13.0 detected; cached gsplat extension
  loaded successfully. CPU-absent/mocked GPU detection and override tests passed.
- Three real 500-step video training smoke runs completed (two previews-off,
  one previews-on), each producing 126,024 Gaussians, peak recorded 0.68 GiB.
  Export PSNR varied 18.400–18.588 dB and SSIM 0.7794–0.7820; these unconverged
  runs verify execution, not quality improvement or preview performance.
  Geometry snapshots are therefore opt-in for demo until a complete rehearsal.
- Real HEIC readability/preflight passed. A five-second excerpt of the preserved
  capture decoded to 20 valid 960×540 frames; adaptive ingest staged all 20.
  The original source was only read.
- Native GLB export validated real preserved Polycam triangle geometry with
  unchanged vertex/triangle counts and bounds. This verifies export, not a new
  mesh reconstruction or segmentation result.
- Real browser dashboard displayed the library and correct workstation warning.
  Actual saved video reconstruction loaded and rendered full PLY/SH2 shading,
  with working saved camera controls. Screenshot retained in ignored QA output.
- Existing main checkout and its untracked status PDF were preserved throughout
  isolated implementation. Original captures and sibling repositories were untouched.

## Remaining risks and manual rehearsal

- **Docker Linux engine remains unavailable on this host.** Starting Docker
  Desktop did not establish its backend. Fresh COLMAP and full input-to-archive
  rehearsal could not be completed; preflight correctly blocks this path.
- **RTX A6000 has not been physically tested.** Configuration supports its
  capabilities, but runtime, CUDA wheel installation, throughput and quality
  require a rehearsal on the actual machine.
- No clean full CUDA environment reinstall, Mac/Xcode build, iOS end-to-end
  capture, physical SpaceMouse test or new segmentation-to-Poisson mesh run.
  Optional local TRELLIS/Hunyuan adapters and weights are not installed.
- Existing integration tests skipped platform-dependent symlinks/live external
  services. Smoke checks do not establish full-training quality equivalence.
- Full-quality viewer retains the branch's PLY loading behavior and also reads
  the compact derivative for framing; large-model browser memory remains a risk.
- Cancellation during Python training, scoring and COLMAP is cooperative;
  archive copy/native operations may finish before the next cancellation boundary.
- Saved-run resume verifies hashes/inventory but is stage-level recovery, not
  restoration of optimiser state from a partially completed training stage.

Before September 30: repair Docker, run one fresh full capture on each GPU,
measure all recorded stages, evaluate cleanup candidates against held-out
images, visually review any object GLB, and rehearse with networking disabled.
