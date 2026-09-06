# Live construction for demonstrations

The capture library now links to **Watch live construction**. After the next
dashboard restart it uses the built-in `/api/construction` read-only endpoint.
It follows normal model training and isolated experiments without adding them
to the Master-only library. Updates are read every three seconds.

For the already-running dashboard, a separate read-only monitor is available
at http://127.0.0.1:8768/. It leaves training and the main dashboard running.
Start it independently with `python -m vitrine.live_build --port 8768`.
The monitor page also falls back to that local endpoint while an older
dashboard is still running.

New training processes save a small JPEG of an existing evaluation render
at each evaluation checkpoint and at final evaluation. They reuse the same
held-out camera and add no extra GPU rendering or changes to the objective.
Files live in `model/construction/` or the experiment's `construction/` folder.
Saving images incurs a CPU transfer and JPEG encoding cost; this overhead has
not yet been benchmarked. Failed preview writes are logged without aborting
training. Snapshot publication uses temporary files and rename.

**Follow live** selects the latest real snapshot. The timeline and **Replay
construction** show saved snapshots in order, explicitly labelled as recorded
renders. This is an evolving image preview, not a continuously editable 3D
model or a fabricated construction animation.

Already-running Python processes do not acquire this code change. Their
numeric progress is visible, but they may have no image sequence. A fresh
training run is needed to verify checkpoint capture end to end; existing
outputs must not be relabelled as earlier training snapshots.

Verified against the real video experiment: HTTP activity endpoint, running
and completed training records, browser progress updates and visual layout.
Python/JavaScript syntax checks passed. No training was restarted, no model
or input was modified, and the main dashboard was not stopped.
