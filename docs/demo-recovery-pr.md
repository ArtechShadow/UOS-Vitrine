# Recovery candidate for local reconstruction and A6000 rehearsal

Target: `codex/demo`
Head: `codex/demo-ready-20260910`

## Why

The September demonstration requires real photographs/video to produce a saved
Gaussian scene, exact local object isolation and a supported object mesh, with
preservation evidence retained. The starting branch could lose old sidecar
outputs, mix ingest filenames, mis-handle calibration and report incomplete
worker/output states too optimistically. Historical object meshes failed visual
review; their existence and texture validity did not establish shape fidelity.

## Changes

- Preserve old and failed sidecar generations and record subprocess failures.
- Preserve colliding stills, normalise EXIF before COLMAP, retain exact image
  identity/pre-crop calibration and correct rational lens rectification.
- Keep selected source media in the archive and reject conflicting source names.
- Add supported-view object reconstruction contracts and selected-object routing.
- Harden full-SH save verification, stage recovery and dashboard state reporting.
- Extend Windows launch/preflight and add reproducible rehearsal/readiness reports.

Implementation integration and exact final test evidence are recorded in
`docs/demo-recovery-report.md`. Deterministic tests verify software contracts;
they do not certify actual object reconstruction quality.

## Acceptance

Candidate for A6000 rehearsal; visual acceptance pending. No GPU, private Lab
capture, installed external SAM2 runner or Windows host exists in the task
runtime. The external runner must meet the documented exact mask/camera
contract. No cloud reconstruction, paid API, vendored model code, capture media
or weights are introduced. Whole-room meshing remains a separate unrun task.

The A6000 is the priority acceptance target. Low/Medium/High efficiency labels
refer to the RTX 3060 Laptop / RTX A6000 / RTX 5090 respectively; performance
comparison requires matched source hashes, settings and evaluation splits.

Please run the commands and visual checklist in `docs/a6000-rehearsal.md`.
Do not merge on the strength of software tests or a structurally valid GLB alone.


## Validation

178 passed, 5 skipped on Linux CPU (Python 3.12.14). Baseline: 113 passed,
5 skipped. Skips require real capture/live dashboard or private Polycam media.
Python compilation, JS syntax, Ruff fatal-error checks, diff checks and static
HTTP smoke pass. Chromium absent; WebGL/browser/offline interception not run.
The exact published tested commit and tree are recorded in this PR body.

Remaining: Windows/GPU and real capture quality, external SAM2 evidence
contract, experimental CPU Poisson normals/surface fidelity, descendant process
cleanup after sidecar timeout, HDR tone-map provenance and Windows original-name
case collisions. Readiness remains blocked and human approval pending.
