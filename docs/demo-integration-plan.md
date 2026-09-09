# Demo integration plan — 9 September 2026

The integration starts from main `03ac9ba6570097a1ff807077ebed9321aea99d5f`.
Rollback branch: `backup/pre-demo-main-2026-09-09`.
Demo tip: `ccc36bd8eb0cbb52db720be8f39cb83d16597063`, also containing
`83f364608270327e39f7b5a8d0df56091896bca5`. Main is an ancestor; there are
no text merge conflicts. Work is isolated on `integration/demo-merge`.

Three Luna Max audits covered merge/GPU, ingest/quality/object providers, and
demo/UI/recovery. This plan was consolidated before implementation.

## Audit findings by subsystem

| Area | Branch changes and integration action |
| --- | --- |
| Ingest | Live frame previews added; existing 200-per-video selection and 600-frame extraction ceilings retained. Add adaptive selection, full-duration sampling, EXIF camera grouping and unique video groups. |
| Preprocessing | Existing undistortion and uint8 CPU image cache retained. Add memory preflight and measured timing. |
| Segmentation | Existing shell-free external SAM2 contract retained. Missing optional sidecar must not prevent app startup. |
| COLMAP | Log observation and mapper snapshots added; fixed eight CPU threads and Docker assumptions remain. Record substages, expose CPU fallback and support cancellation. |
| gsplat | Main objective, camera geometry, MCMC and export guards retained. Branch adds observational preview copies; runtime impact requires measurement. |
| CUDA/GPU | No demo edits to cuda_toolkit or profiles. Add generic capability records and validated overrides; never assign a 5090 benchmark to an A6000. |
| Viewer | Full-quality rendering and construction replay added. Fresh CLI pipeline omitted scene.splat export; add explicit validated export. |
| GUI/launcher | Native wrapper and richer construction UI added. Fix mandatory sidecar startup, unbounded RAM uploads and recovery controls. |
| iOS | Preserve apps/ios-capture and CLI session validation/import. Dashboard import remains future; no Mac/Xcode dependency for demo. |
| Mesh | Optional Poisson derivative added. Keep independent; malformed optional output must not break scene packaging. Add a file-based local image/mask-to-GLB provider seam. |
| Dependencies | Optional pywebview 6.2.1 and pymeshlab requirements added. No experimental ML dependencies in core. Record the tested CUDA package versions. |
| Configuration | Existing profiles include below-safe-coverage configurations. Preserve historical numbers; formalize the separately measured video demo recipe. |
| Paths/environment | Root detection and Windows tool discovery added; launcher injected a local SAM2 path and fixed radio prompt. Respect explicit configuration. |
| Tests | Mobile contract and multipart tests added. Baseline: 128 passed, 15 skipped, one error-text mismatch; focused syntax/undefined-name check passed. |
| Documentation | Capture-session, launcher and construction docs added. Align readiness claims with runtime evidence and keep historical results scoped. |

## Implementation order

1. Hardware/configuration and adaptive ingest as independent modules.
2. Mandatory preflight, stage-level timing, durable state and exclusive run lock.
3. Explicit viewer export, optional derivative cleanup and isolated provider interfaces.
4. Transactional archive publication, upload safety, launcher and operator recovery.
5. Regression/static/import checks and real-media/browser checks, then a merge report.

## Acceptance boundaries

- Preserve source/reference bytes, existing captures, the untracked status PDF,
  original main history and both demo commits. Never edit sibling repositories.
- Existing trainer quality settings remain intact; cleanup candidates never replace masters automatically.
- Test actual RTX 5090 CUDA and cached gsplat. A6000 runtime/quality requires that hardware;
  a capability override is a configuration test, not an A6000 benchmark.
- Record stopped Docker/absent optional provider failures visibly. Never label
  an unavailable mesh model as installed or a stored reconstruction as a fresh rehearsal.
- Report all skipped/manual checks before merging. Do not force-push or rewrite main.
