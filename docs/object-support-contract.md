# Local object reconstruction evidence contract

This is the in-repository file interface for the separate SAM2.1 adapter, not
an installation of SAM2 or a claim that the Lab runner already emits it.
External model code stays in its own process/environment. No sibling repository
was inspected or edited in this task.

## Required association and lineage

The canonical `objects/objects.json` remains `vitrine/object/1`. Each selected
Gaussian object retains its original world coordinates and supplies:

- `object_id`, `label`, `splat_path`, and the viewing asset's `sha256`.
- A sibling full-SH PLY (for `candidate_01/observed.splat`, use
  `candidate_01/observed.ply`), whose checksum is in
  `provenance.source_asset.sha256`. A viewing derivative is never a substitute.
- `provenance.source_scene.sha256`, the exact current `model/scene.ply` used to
  carve that object. A stale archive scene or different checkpoint is rejected.
- `provenance.evidence`, containing at least three genuine calibrated views
  observing the chosen object, with angular diversity. Every record carries
  explicit COLMAP `image_id`, exact `image_name`, `camera_id`, `instance_id`
  and/or `detection_id`, actual `mask_path`, and `mask_sha256`.

Image IDs are not list indices. Keep a video/source frame ID separately as
`source_frame_id`. Emit both detection and instance IDs when available. Within
one image, different detections of the same label must never substitute for
one another. Do not associate masks by label, basename or list order.
Masks are paths relative to `objects/` after core import. The importer can
compute the hash of a supplied real mask and records that fact; canonical
reconstruction requires and verifies the resulting checksum. It never creates
a mask from a crop/thumbnail or guesses a missing association.

The importer accepts explicit evidence and source-scene lineage from external
object/candidate records (and scene lineage from the source manifest). Incomplete
records remain inspectable Gaussian candidates with missing-evidence diagnostics;
they cannot satisfy supported object-surface reconstruction.

## Coordinate contract

Every evidence record explicitly declares `coordinate_space`:

- `view`: the entire calibrated image raster before the core's lens
  rectification. It must use the same orientation as COLMAP input. Newly
  ingested images have EXIF orientation normalised in their pixels. The core
  resizes the mask with nearest sampling to the retained pre-rectification
  dimensions, then applies the same lens mapping and crop as the image.
- `rectified`: already in the final core ViewSet raster. Supply
  `transform.output_size: [width, height]` and `transform.intrinsics` (3×3 K,
  also accepted as `transform.K`). Both must match the chosen mesh-resolution
  ViewSet. Matching dimensions alone cannot establish a matching crop.

Do not pass a detector crop as a full-frame mask. Restore it to its complete
source raster before handoff, or explicitly produce the matching rectified
mask. Retain source transforms and masks in the separate runner's evidence.
Unsupported or undocumented transforms produce an actionable missing-support
result. Every image uses its own COLMAP camera model and pose.

## External runner work required

The currently installed private `run_sam2_local.py` was absent here. Its owner
must update/verify it to retain the exact selected detection/mask identity from
identification through carving and seed selection, and emit the records above.
It must hash the actual scene checkpoint it read, preserve full-SH object PLYs,
and retain raw masks. The previously documented frame-only seed selection is
not sufficient. This is an external contract requirement, not a claim that an
upstream patch or Lab installation was performed.

Install/download models online first, retaining code/model licences and version
records. The adapter must respect offline operation and fail if a required local
file is missing. Environment variables alone do not prove that an uninspected
external runner makes no network requests.

## Reconstruction and output

```powershell
& $Python -m vitrine --run-dir $RunDir object-meshes --object-id candidate_01
```

Use the actual object ID from this run. This command is also exposed on each
object card in the dashboard. The supported path uses masked, finite positive
camera-Z depths, opacity/visibility, the verified full scene for occlusion,
multiple-view agreement, observed bounds and post-mesh support tests.
Only selected, calibrated observations are fused. Scene-wide views are never a
fallback. Gaussian scale/outlier checks are diagnostics/rejection, not evidence
that COLMAP world units are metres or permission to silently remove thin parts.

The current backend is explicitly experimental screened Poisson via optional
`pymeshlab`. It remains a CPU surface step after local GPU depth rendering.
Its volumetric-obscurance scalar is not measured camera support. MeshLab normal
orientation and real object quality remain unverified on the Lab capture.
No Open3D/TSDF or generative completion backend was introduced. Existing
separate optional image-to-mesh providers remain available and do not satisfy
the observed-surface acceptance gate merely by generating a GLB.

Immutable generations are published under `object-meshes/published/`; only the
verified generation referenced by `latest.json` is the current candidate.
Previous generations and failed staging evidence remain available. GLBs contain
observed vertex colour, not a claim of calibrated PBR textures. Inspect geometry
from the captured viewpoints, disclose uncovered surfaces, reopen exports and
verify the preservation package before recording human acceptance.

Strict evidence checks may reject historical candidates or valid thin structures;
thresholds and expected-depth reliability require a real-capture rehearsal.
An insufficient-support result is not successful reconstruction, and these
software contracts do not establish high-fidelity shape on their own.
