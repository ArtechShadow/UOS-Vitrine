# Live construction evidence

Live construction follows real processing outputs, with stage buttons for
reviewing completed work and **Follow live** for returning to the active stage.

- Video extraction publishes small JPEG previews as ffmpeg writes frames.
- Image selection shows pending, kept and rejected thumbnails, camera groups,
  and recorded rejection reasons. Kept means the image was successfully staged.
  Originals remain unchanged. Pages and filters keep large captures usable.
- Camera reconstruction shows the latest image and feature count reported by
  COLMAP, followed by recorded camera positions and sparse geometry.
- Training uses the existing recorded Gaussian previews and replay timeline.
- Evaluation shows available comparison renders and distinguishes training
  checkpoints from the separate saved-model evaluation report.
- Packaging shows file copying and checksum inventory progress. Writing a
  manifest does not imply independent checksum verification has passed.

The ingest sidecar is `ingest/selection.json`; its preview JPEGs live in
`ingest/selection-thumbnails/`. Packaging observations are written outside the
archive to `construction-package.json`, so observation does not alter the
archival inventory. Old runs show missing-evidence states rather than invented
intermediate results.

`scripts/watch_ingest_preview.py` can observe an already-running, video-only
capture made before native previews were added. It derives kept images from
actual staged files and only finalises rejected decisions after ingest writes
its report. Unrecorded rejection reasons are explicitly labelled as such.

## Windows and Docker database boundary

Do not query COLMAP's live SQLite database from Windows while its Linux
container is writing it. A nominally read-only SQLite connection can still
interact with shared-memory/WAL coordination. During the XR Lab rehearsal on
9 September 2026, a Windows feature observer was followed by a matcher SQLite
disk-I/O failure. The observer may have contributed; this was not a controlled
causality test. It was removed entirely. Visual feature reporting now reads
COLMAP's flushed log and never opens the database.

After stopping the aborted matcher, a separate copy of the database plus WAL
passed SQLite integrity checking and retained all 200 feature records. Original
database/WAL/shared-memory files were preserved before replacing the failed
working set with the checked copy. Matching resumed successfully using existing
features. Never replace a database while any processing stage still owns it.

## Verification

`scripts/verify_ingest_progress.py RUN` uses eight actual extracted frames from
the supplied run and a four-frame sample of its actual video. It checks that
adding observation does not change selection, that thumbnail records match
staged results, that source hashes remain unchanged, and that extraction and
file-copy callbacks report actual outputs. Scratch results remain under `tmp/`.
