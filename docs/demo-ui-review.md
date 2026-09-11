# Presentation workspace review — 6 September 2026

The local dashboard now keeps Vitrine's dark, warm-orange branding while
presenting a direct Images → 3D splat → Objects journey. This is the preservation
pipeline dashboard, not a change to the separate Capture product.

Desktop shell follow-up: the sidebar collapses to an icon rail (saved in
`vitrine.ui.sidebar`) and auto-collapses on Live construction. Simple mode keeps
Create, Library (Scene / Object) and Live construction; Quality guide and
Workstation appear only in Advanced view. Object capture is a first-class
library page, including items separated from scenes. Appearance lives once in
the shell — construction no longer repeats it when hosted in the dashboard.
An XR Lab watermark in the bottom-right shows the package version and, when
the doctor payload includes it, the git branch.

## Delivered

- Image-led capture library, search and ready filter; real titles and photographs.
- Master-only demo selection; older captures remain on disk.
- Photograph inspection, large interactive splat workspace, three real camera
  viewpoints, reset, fullscreen and presentation mode with Escape recovery.
- Locally bundled renderer libraries and fonts, with licence notices.
- Upload previews, retained draft selection, duplicate-submit prevention and
  processing/failure status. Capture subprocess output is preserved in capture.log.
- Six actual observed Gaussian candidates from the separately installed SAM2
  sidecar, source crops clearly labelled, source-camera viewing, downloads,
  SHA-256 validation and provenance. These are not complete meshes; edge
  fragments and duplicate equipment candidates remain visible.
- Optional SpaceMouse connection using WebHID, movement speed selection,
  six-axis camera movement and first-button reset. This independent integration
  requires browser device permission. Hardware motion still requires a user check.

## Open the demo

From the project root, after stopping any previous dashboard on port 8765:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_demo.ps1 -WithObjects
```

Open http://127.0.0.1:8765/. The launcher shows only nested-cinema-04-master.
It retains the separate installed sidecar; see sam2-object-sidecar-setup.md for
its paths and the current machine-specific installation limitation.

For SpaceMouse, open the splat viewer, choose Connect SpaceMouse and select the
device. Chrome or Edge with WebHID is needed if the embedded browser does not
offer device access. A connected receiver was detected locally; this does not
alone prove cap movement works. Navigation stops when focus is lost or input
reports cease. No 3Dconnexion certification is claimed.

## Verification

- Real master renders with external browser networking blocked; no console errors.
- Desktop presentation view visually inspected at 1440×1000.
- Presentation mode and Escape exit passed; mobile object layout has no horizontal
  overflow at 390×844. Object thumbnails constrained to their preview area.
- Real JPEG selection and drag/drop validation passed; no new upload was submitted
  from the UI smoke test. Draft title/file selection survives Library navigation.
- Configured sidecar HTTP smoke: 2 passed, 1 intentionally skipped. All six real
  output assets downloaded. The skipped test must not launch a configured GPU job.
- Sidecar executed twice on 40 real views; pipeline times 54.53s and 51.74s,
  excluding model loading and final export. Both produced the same six carve counts.
- Python/JavaScript syntax and git diff whitespace checks passed. This is not a
  claim that the full test suite or fresh capture pipeline has passed.

Screenshots are under output/playwright, including studio-final-present.png and
studio-object-source-view.png. Source-camera inspection shows recognisable reel
equipment with substantial surrounding splat fragments. Source crops must not
be presented as screenshots of clean 3D geometry.
Candidate 02's source crop shows a portable boombox while its carved 3D view
contains the nearby television/deck stack. This is an observed separation
quality failure, not merely a framing issue. Hash-valid output does not prove
correct object identity. The Objects stage is an experimental candidate review.

## Remaining acceptance gates

The master still lacks a separate canonical evaluation report. Stored training
metrics are identified as such; the web splat is an SH0 viewing derivative of
the full-SH master. A new video-only quality experiment is separate from this
master; [its matched results](demo-video-quality-20260906.md) are now complete.
The selected result is included in the full library at http://127.0.0.1:8765/.
The default demo launcher now exposes all captures; use its Run argument to
restrict a rehearsal to selected captures. The UI uses canonical saved-PLY evaluation
when present, with explicitly labelled training metrics as the fallback.
New-capture processing through the upload UI, failure recovery,
physical SpaceMouse movement, and cold startup on the venue display require
rehearsal before signing off the 30 September demo.

## Library management and SpaceMouse follow-up — 6 September

The default launcher exposes all 19 recognised capture folders. Capture detail
has Rename and Delete; the library has Trash. Rename writes run-label.json and
leaves original preservation metadata and folder names unchanged. Delete asks
for the named capture and moves its folder into runs/.trash with a unique suffix.
Restore refuses to overwrite an existing folder. Current dashboard capture and
object jobs, and training marked running, block deletion. This does not constitute
process discovery for jobs started outside this dashboard; finish external jobs
before managing their folders. Trash is recoverable and does not free disk space.
Source and reference folders are outside these operations.

Verified HTTP rename/invalid name/missing confirmation/trash/list/restore using
a disposable copy of the real video capture metadata in tmp; restored bytes
matched the original. No existing run was deleted. Browser checks verified the
19-item library, rename form, cancelable deletion confirmation and empty Trash;
no browser errors were reported during those checks.

SpaceMouse now reads every motion packet directly from its DataView and opens
all authorised interfaces of the selected receiver, instead of assuming the
first interface carries motion. The user confirmed movement after this change.
The subsequent navigation tuning defaults to slower movement with a locked
horizon, and exposes reverse movement, reverse turning and turn speed controls.
This final tuning still needs user acceptance on the physical device.

## Library covers and timestamps — 6 September

All six captures remaining after the user-directed Trash cleanup now have actual
splat render covers. Four were rendered from their saved PLY files; two older
runs use their existing matched-view comparison renders, recorded in each
model/library-hero.json. No reconstruction or source media was altered.
The hero metadata ties the preview to the model modification time, so replacing
the model invalidates the old cover. scripts/render_library_heroes.py can render
covers for additional runs; regeneration is manual, not an automatic GPU job
launched by visiting the library.

Cards and ordering now use splat_created_mtime from scene.ply, falling back to
scene.splat. This is the model file's export/write time, the available creation
record for legacy captures; a filesystem copy that changes that timestamp would
need provenance-based repair. Rename, packaging and cover generation do not
change this timestamp. Captures without a model show 'Splat not created yet'.

Live verification: all six hero URLs returned HTTP 200, all six card images
loaded in the browser, and timestamps matched model files. Python and JavaScript
syntax checks passed. The rendered-cover contact sheet was visually reviewed.
