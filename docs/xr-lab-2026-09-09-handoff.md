# XR Lab rehearsal: saved progress, 9 September 2026

Work stopped at the user's request. This is a development checkpoint for
`codex/demo`, **not a declaration of readiness for the 30 September demo**.
No further reconstruction, meshing or material baking is scheduled.

## Saved result

The local run is `runs/xr-lab-20260909-hq/`. Original capture media remains in
`source/xr-lab-20260909/`; the original and preserved copy were checksum matched.
Raw media, models, weights, logs and screenshots are retained locally and are
not included in this Git commit.

- Prepared 521 usable frames from a 130.5-second HDR phone video.
- Recovered the main camera component: 448/521 registered views (85.99%),
  80,800 sparse points, mean reprojection error 1.18 pixels.
- Selected the saved 25,000-step full-SH3 checkpoint as the current master:
  1,212,000 Gaussians, **23.5019 dB PSNR / 0.8530 SSIM** on the same 56 held-out
  views at 1600 pixels. These are saved-file measurements, not training previews.
- Master `model/scene.ply` SHA-256:
  `42ccb053ec9530931e7d61313ac17e5e71700c2c52284182ef59d52463d35cd1`.
- Export, initial preservation package and checksum verification completed.
  The initial archive does not yet contain the complete rehearsal evidence or
  final mesh deliverables.
- The splat viewer opens from real captured viewpoints. Workstations and room
  structure are recognizable; thin geometry, incomplete coverage and streaks
  remain visible. Exterior orbit views are not an accepted fidelity test.

The requested 30,000-step run did not produce its final saved PLY. The process
disappeared during finalization without a recorded cause; earlier checkpoints
were preserved and compared independently. Do not report the selected master
as a completed 30,000-step result.

## Object and mesh status

Local GroundingDINO + SAM2.1 large separation ran in an isolated external
process. The refined run used 160 views and retained a wooden cabinet and an
upholstered seat, with source crops, mask evidence and checkpoint lineage.
Original and refined candidates are preserved separately.

Both initial object meshes were baked into GLBs with embedded 4096-pixel
base-colour, normal and packed occlusion/roughness/metallic textures. Structural
checks passed; **both models failed visual review**. Large false surfaces and
distorted geometry prevent them from being accepted as HQ object models.
Texture presence is not evidence of shape fidelity. Roughness and metallic
values are estimates; captured colour still includes scene illumination.

The cabinet's isolated Gaussian subset contains severe spatial outliers.
The current experimental mesher uses scene-wide viewpoints and lacks an
object-support bound. A proposed correction has **not** been implemented or
run: use supported object views, bound fusion to observed geometry, and reject
unsupported Poisson surfaces before rebaking. Preserve the failed attempts.

The whole-room scene mesh has **not been run**. Its rehearsal script is saved
for later work and is not an accepted output.

## Changes saved with this checkpoint

- HDR ingest tone mapping with provenance; camera-coverage acceptance checks;
  largest-component selection and truthful matching/preprocessing progress.
- Independent evaluation integrated into the recoverable pipeline.
- Atomic, chunked full-SH PLY writing; save the completed model before final
  evaluation. A real 1.212M-Gaussian round trip was checked; the revised final
  save path has not been exercised by a fresh complete training run.
- Full-SH object-source checksum validation, retained object provenance,
  configurable mesh settings, and verified immutable mesh publication that
  tolerates Windows file handles. Failed staging outputs are retained.
- Captured-view navigation, enabled object workspace, local GLB inspection
  viewer and postprocessing status plumbing. The newest mesh viewer and status
  additions still require browser runtime validation.
- Rehearsal-specific recovery, comparison, selection, meshing, Blender baking
  and GLB validation scripts. These are experimental capture-specific tools;
  the Blender runner currently assumes the Windows Blender 5.1 install path.

Global hardware profile values and the baseline training algorithm were not
retuned. The successful opacity experiment is recorded as a run-specific
override. External model code and weights are not vendored into this project.

At save time, the ingest, pipeline reliability, object CLI, object validation,
object packaging and object HTTP tests passed: **75 passed, 4 skipped**. Python
and JavaScript syntax checks and first-party Git whitespace checks also
passed. Unmodified upstream Three.js files retain their original indentation.
These checks do not certify the untested browser additions or rejected meshes.

## Resume evidence and remaining work

Start with the local `report/xr-lab-20260909-rehearsal/README.md` and offline
`report.html`. Run evidence includes `quality-comparison/`, `attempts/`,
`segmentation-refinement/`, `objects/`, `object-meshes/`, `pbr/` and `evidence/`.
The two rejected PBR inspections and exact hashes are preserved under
`evidence/pbr-attempt-01-visual-rejection/`.

`evidence/reproducibility/` records code, environment and run metadata. It is a
post-training snapshot that includes subsequent fixes, not a byte-exact claim
about the historical training process.

Before calling the full demo ready: correct and visually validate both object
surfaces, reconstruct and inspect the scene mesh, exercise the new viewer and
final-save path, complete cold-start/offline rehearsal, and package the full
provenance and accepted outputs. A6000 execution was not tested on this host.

The dashboard may remain open for inspecting existing results. No automatic
resume or expensive job is pending.
