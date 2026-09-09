# Adaptive ingest

Video ingest uses the `balanced` selection preset when no explicit video
budget is supplied. The selector scores each decoded frame with four cheap
signals: Laplacian sharpness, exposure/clipping quality, frame-to-frame
novelty, and temporal position. Frames with unusable exposure or a near
duplicate predecessor are rejected, then the remaining frames are divided
into temporal buckets so a sharp section cannot consume the whole budget.

The presets are deliberately bounded:

| preset | target views/s | minimum | maximum | intended use |
| --- | ---: | ---: | ---: | --- |
| `fast-demo` | 1.5 | 48 | 180 | short venue rehearsal |
| `balanced` | 2.5 | 96 | 360 | normal local ingest |
| `archive` | 4.0 | 160 | 900 | higher coverage archive candidate |

The derived extraction directory has a global 2,400-frame safety cap. The
duration is read with `ffprobe`; when a clip would exceed the cap, extraction
lowers its FPS and retains the full timeline. A final evenly spaced guard
handles variable-rate or incomplete metadata. This avoids the old behaviour
where `-frames:v 600` stopped a 4 fps decode at the first 150 seconds.

`video_budget=<positive integer>` is an explicit compatibility mode. It keeps
the historical 600-frame extraction window and Laplacian-only sharpest-frame
selector, and is recorded in `ingest.json`, so an earlier run can be
reproduced. `video_budget=None` selects the adaptive path and
`selection_preset` must be `fast-demo`, `balanced`, or `archive`.

`classify_sources` includes the normalized EXIF camera label in its grouping
key `(folder, width, height, camera_label)`. This prevents two camera bodies
with the same dimensions from sharing one COLMAP intrinsics group. Missing
EXIF remains a visible warning. Video extraction directories include a short
source-path digest, and published group names are made unique when stems
collide.

The report contains per-group selection counters plus aggregate counters and
phase timings. It records whether a video's safety cap was used and the actual
FPS, making a demo run inspectable without reproducing the decode.

## Optional cleanup candidate

`vitrine.cleanup.cleanup_splat(source, target, ...)` creates a new PLY and a
`<target-stem>.cleanup.json` manifest. It refuses in-place writes and existing
targets, and records SHA-256 hashes for both master and derivative. Defaults
remove only non-finite rows and rows below the activated opacity floor. Spatial
removal requires explicit `scene_bounds=(lower_xyz, upper_xyz)` or both
`scene_scale` and `radius_multiple`; there is no default radius, component, or
thin-geometry heuristic. The returned status remains a candidate requiring
held-out render and visual validation before publication.
