# Splat quality review for the 30 September demo

Initially reviewed 5 September 2026 against the code, real Nested Cinema master artifacts, and recorded experiments. That review did not launch training or change source/reference artifacts. The subsequent authorized video-only experiment and measured pipeline corrections are documented in [demo-video-quality-20260906.md](demo-video-quality-20260906.md).

## Findings

The existing master is the appropriate baseline. Its current `model/train.json` records **22.906 dB PSNR / 0.7781 SSIM** over 92 held-out views at the trainer's 1600-pixel evaluation limit, with 644 training views. These are recorded scores, not a fresh measurement. `model/diagnosis.json` records **22.438 / 0.7487** at the diagnostic script's 2304-pixel limit. Those two numbers should not be compared without explaining resolution.

A fresh CPU inspection of the actual `scene.ply` reconciles its health with the stored report: 2,000,000 Gaussians, **70.34515% opacity > 0.005**, median live axis ratio **52.11964**. The export uses a different threshold, opacity >= 1/255, which retains 83.9142% before spatial culling. The actual `.splat` contains **1,615,238 records**, all with nonzero alpha. Differences between these counts do not indicate missing training output.

The browser is showing a lighter derivative, not the scored full model. `export.py` writes band-zero colour to `.splat`, and the viewer selects spherical harmonic degree zero. The full SH3 PLY retains view-dependent appearance; its PSNR/SSIM cannot be presented as a direct browser-rendering measurement. The derivative also quantizes colour, alpha and rotation. Its current coordinates span roughly -83 to +86, so framing the whole bounding volume is a poor way to open this interior.

At the initial review, generic creation presets did not reproduce the validated master recipe. `train.py` had `APPEARANCE_OPT = True`, whereas the measured master script explicitly disabled it, and the recorded controlled study found a 1.17 dB loss with that appearance model. The subsequent video-only comparison justified changing the default to False. The workstation presets still have substantially smaller crop/source ratios than the master. Do not call any stock preset equivalent to the demonstrated master without a new benchmark. Changing these defaults requires measured validation under AGENTS.md.

The master has training and per-group diagnostic evidence but no canonical evaluation report recognized by the dashboard. Complete that evidence path using a fresh evaluation with explicit split, resolution, model hash and software versions; do not merely mark the stage complete or copy metrics between report schemas.

## A reproducible opening view

Visually inspected staged capture `video_IMG_6318/frame_00233.jpg` shows the sofa, rug, radio and far wall in a broad interior composition. Its actual registered COLMAP pose yields:

```json
{
  "position": [-4.923625946, -0.885919333, -1.784598708],
  "lookAt": [-2.181847930, -0.252883986, -0.744444937],
  "up": [0.215030000, -0.976225138, 0.027323356],
  "verticalFov": 65.451039606
}
```

Position is `-Rᵀt`, forward is `Rᵀ[0,0,1]`, and up is `Rᵀ[0,-1,0]`. Look-at lies three world units forward. These apply to the untransformed master geometry. The FOV uses the registered camera's height and focal length and is an approximate symmetric framing because the original lens has distortion and an offset principal point. Final browser visual verification remains necessary.

Full precision and two alternatives are in ignored `output/quality-review/viewer-cameras.json`; source contact sheets are in that directory. Saved opening/reset viewpoints should reference the actual capture and remain separate from changes to training quality.

## Experiments in priority order

| Priority | Experiment | Measurement and acceptance |
|---|---|---|
| 1 | Open the existing master from the registered overview camera and add reliable reset. | Cold-load repeatedly; inspect sofa edges, newspaper walls and radio while moving between overview and captured detail views. Record loading time and navigation smoothness. This improves presentation without retraining. |
| 2 | Re-evaluate the current PLY and generate held-out reference/render comparisons. | Freeze the same 92-view split; score at 1600 and 2304 separately, record all five camera groups, hash the input PLY and retain image comparisons. Reconcile with the stored baseline before trying changes. |
| 3 | Measure browser derivative loss. | Compare SH3 versus SH0 at identical held-out poses; then test a supported SH-preserving browser format on the presentation machine. Record image quality, file size, peak memory, cold-load duration and frame rate. A larger master is not automatically a better live demo. Preserve current derivative as fallback. |
| 4 | Validate the master recipe through the normal creation path. | Use a distinct run directory, identical registered inputs/split/seed and the recorded 2304-source, 1536-crop, 2M-cap, 30k-step, 15k-LR-horizon recipe with appearance compensation off. Confirm PSNR or SSIM improves versus the actual stock preset without unacceptable regressions; benchmark before shipping profile/default changes. |
| 5 | Diagnose the weak portrait camera group and try a better pose solve. | The stored iPhone 14 Pro group scores 18.550 / 0.6107 at 2304 versus 22.956 / 0.7381 for the main stills. Inspect reprojection residuals, blur, intrinsics and overlap first. Run a separate higher-resolution/denser SfM candidate, preserving multi-camera references; hold training recipe and evaluation protocol fixed. A gain is a hypothesis, not an established result. |

For every candidate, preserve the accepted master, use a separate output directory, retain registration coverage and per-group results, and reject phases that improve neither PSNR nor SSIM. Changes to the input view set require a shared comparison set; aggregate scores from different captures are not a quality ranking.

## Avoid repeating failed work

- Raising source resolution alone is not justified. The documented 3072/2048 challenger lost to 2304/1536 even with healthy coverage, at both comparison resolutions.
- Do not increase splat count merely because GPU memory is available; throughput and useful observed geometry set the budget.
- Keep undistortion and SSIM enabled. Preserve per-image camera intrinsics.
- Do not tighten export scale or spatial culling based only on appearance from one view. The existing guards were measured; evaluate any proposed change against held-out views.
- The historical video-only `nested-cinema-02` score uses a different dataset from the mixed-camera master and is not evidence it is a better reconstruction of the full installation.

During the subsequent video experiment, the coverage diagnostic was corrected to average `min(crop,width) × min(crop,height) / (width × height)` over the loaded views, matching the actual clipped patches. Previously it used `crop² / mean(frame area)`, which incorrectly reported 79% for a 1536 crop on a 2304×1296 video frame; the actual coverage is 66.7%. This changes reporting only, not the sampler or training settings.

## Evidence and runtime scope

Primary local evidence: `docs/nested-cinema-04-master.md`, `scripts/run_nested_cinema_04_master.py`, `scripts/diagnose_run.py`, `vitrine/train.py`, `vitrine/export.py`, `vitrine/ui/viewer.html`, and the master's recipe, train and diagnosis JSON files. Current sparse model parsed successfully: 736 images, five OPENCV cameras and 404,570 points. CPU inspection used `C:\Users\realg\miniconda3\envs\vitrine-train\python.exe`; the project `.venv` could not create its referenced Python process in this review. That runtime observation does not establish a GPU-training failure or success.
