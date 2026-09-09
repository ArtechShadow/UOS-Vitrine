# Live construction workspace

The dashboard has a shared Light / Dark / System appearance preference, a Live
construction navigation entry, and a construction workspace inside each capture.
Successful uploads open that workspace automatically. The legacy live-build
page links into the same interface; the standalone monitor remains available.

## What is shown

- Image preparation: real staged-image counts, without guessing how many images
  will pass selection.
- COLMAP: continuously drained logs, supported extraction/matching counts, and
  camera poses and sparse points from completed mapper snapshots. The installed
  container's help determines whether image or frame snapshot options are used.
- Training: a deterministic sample of at most 250,000 Gaussians, exported every
  five seconds when the previous export has finished. GPU sampling occurs at a
  training-step boundary; encoding occurs on a single CPU worker. This is a
  target cadence, not a measured guarantee on every machine.
- Evaluation: existing held-out renders, with their corresponding source photo.
  No extra GPU evaluation is requested for the interface.
- Completion: an explicit link to the full viewing derivative, plus replay of
  the recorded previews. Older captures never acquire invented replay history.

Orbit, reset, camera overlays, source comparison, replay and fullscreen are
available in the workspace. Following new snapshots preserves the camera pose.
The UI labels reduced-detail splats and recorded images distinctly.

## Files and API

`GET /api/runs/{name}/construction` returns the stage, state, heartbeat, measured
counts, optional total/ETA, training metadata, snapshot URLs and completion
flags. `?experiment={name}` selects a run's experiment. Existing run APIs and
`/api/construction` remain available.

Status is atomically written to `construction-status.json` in each active stage
folder. Normal-run previews share `runs/{name}/construction/live/manifest.json`;
experiments keep their own construction folder. Only manifest-owned preview
files are thinned, retaining the first, latest and at most 120 snapshots per
normal run (or per independent experiment). Archival files and training
checkpoints are never part of this retention policy.

During mapping, a successor snapshot establishes that its predecessor is closed;
the reader does not rely on file size stability. After mapper exits, its final
snapshot can be read. Conversion errors leave the previous published preview
available and are reported separately. Unsupported snapshot flags leave COLMAP
running normally; the final camera model can still be displayed.

Dashboard-owned process exit codes take precedence over heartbeat inference.
For external jobs, an old heartbeat means **status unknown**, not proof that
training failed. The browser polls every two seconds while visible and every
ten seconds while hidden.

Set `VITRINE_LIVE_PREVIEWS=0` to disable periodic mapper snapshots and training
3D previews for overhead comparisons. Existing evaluation renders and status
reporting remain enabled. Reconstruction profiles, loss, distortion correction
and optimisation schedules are unchanged.

## Validation status

Implemented on 9 September 2026. Python compilation/imports, JavaScript syntax,
empty-state APIs and browser checks of the library, creation flow, theme switching
and construction workspace have been performed, including a 390-pixel viewport.

**Real reconstruction acceptance is incomplete.** This checkout has no captured
COLMAP model or trained splat, and the PC's C++ Build Tools and Docker setup is
unfinished. Interactive geometry replacement, measured update cadence and
paired-training quality/overhead have not been verified here.

Once a real capture is available:

```powershell
.venv/Scripts/python.exe scripts/verify_construction.py --run-dir runs/NAME
```

This checks real camera references, atomic publication, interrupted publication,
retention, parameter/RNG preservation and source-file checksums. Reports go to
a unique directory beneath `output/`.

For end-to-end acceptance, run the same real images and training seed into two
separate output directories with previews enabled and disabled. Compare final
PSNR/SSIM, model values, total time, peak memory and observed preview intervals.
In the browser, verify sparse-to-splat transition, repeated swaps while orbiting,
replay, source comparison, interrupted connections and rendered-image fallback.
Finally complete preservation packaging. Record measured results before claiming
the five-to-ten-second target or unchanged reconstruction quality is validated.
