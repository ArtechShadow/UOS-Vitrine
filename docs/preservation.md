# The preservation package

What `vitrine package` produces, what each part is for, and the reasoning
behind the choices.

---

## The problem

A Gaussian splat is a few hundred megabytes of anisotropic blobs. Opened
without context it tells you almost nothing: not what was photographed, not
when, not by whom, not with what, and — most importantly — not how much of what
you are looking at was *observed* rather than *inferred*.

It is also a young format. The viewer that opens `scene.ply` today may not
exist in fifteen years, and the format is still being standardised.

So the splat alone is not a preservation outcome. It is one derivative among
several, and not the most durable one. The package exists to surround it with
the things that make it trustworthy and re-creatable.

---

## Structure

```
archive/
├── originals/      source photographs and video, untouched
├── sfm/            camera poses and sparse cloud, plain text
├── model/          scene.ply — the splat, full spherical harmonics
├── derivatives/    compressed and converted copies for access
├── manifest.json   versions, parameters, metrics, checksums
└── README.md       plain-language explanation, no tooling required
```

### `originals/` — the preservation master

The source files exactly as they came off the camera. Not resized, not
re-compressed, not colour-managed, original filenames.

This is the part that actually matters most. Everything else in the package can
be regenerated from these files; these files cannot be regenerated from
anything. If storage is ever constrained, this is the last thing to go.

Keeping EXIF intact is part of this: it records the camera, lens, exposure and
time, and the pipeline depends on the focal length to seed its solve.

### `sfm/` — the camera poses

`cameras.txt`, `images.txt`, `points3D.txt` and the COLMAP database.

Structure-from-motion is the expensive, non-deterministic and failure-prone
stage. Keeping its solved output means a future re-run does not have to
re-solve it — and can be compared against it. Text format, deliberately: a
future reader needs no COLMAP build to see what the camera positions were.

`cameras.txt` is also where multi-camera captures become legible. A capture
mixing stills and video has two entries, with different intrinsics. That is a
fact about the capture worth preserving explicitly.

### `model/` — the splat

`scene.ply` with full spherical harmonics, in the standard 3DGS PLY layout.
Uncompressed and unquantised: this is the master, and it is the thing every
derivative is made from.

PLY was chosen over the newer compressed formats (SPZ, SOG, KSPLAT) for the
master copy specifically *because* it is old, dull, and widely readable. The
compressed formats are better for delivery and belong in `derivatives/`.

### `derivatives/` — access copies

Compressed splats for web viewing, meshes, preview renders. Everything here is
disposable by design — regenerable from `model/` — and that is what makes it
safe to re-encode as formats come and go.

### `manifest.json` — the record

Machine-readable, and the thing that makes the package auditable:

| Field | Why it is there |
|---|---|
| `software` | Versions of Python, torch, gsplat, COLMAP, the GPU, and the `vitrine` git revision. When a future re-run differs, this is how you find out why |
| `profile` | Every quality parameter used — resolution, splat cap, iterations, SH degree |
| `ingest` / `sfm` / `training` | What each stage did, including how many frames were rejected and why |
| `training.final_psnr` / `final_ssim` | Measured against held-out views the optimiser never saw |
| `files` | SHA-256 for every file in the package |

### `README.md` — for a human

Written for someone opening the folder years from now with no knowledge of the
project and no special software. It explains what the folders are, includes a
self-contained integrity check, and states the limitations of the medium.

---

## Integrity

```bash
python -m vitrine verify runs/<name>/archive
```

Re-hashes every file against the manifest and reports anything missing or
changed. The package also carries a standalone copy of this check inside its
own README, using nothing but the Python standard library — so verification
survives the loss of this tool.

Checksums detect corruption; they do not repair it. They belong alongside a
real backup policy, not instead of one.

---

## What quality figures actually mean

Every 8th view is withheld from training, spaced evenly along the capture path
rather than clustered. PSNR and SSIM are computed against those unseen views.

This measures how well the model predicts photographs it was never shown,
which is a fair proxy for whether it represents the space or merely memorised
its training frames.

It is not a measure of geometric accuracy. A model can score well and still
have invented a surface that was never photographed — reconstruction fills gaps
with whatever makes the training views look right.

SSIM is the more informative of the two for this purpose. PSNR is dominated by
overall brightness agreement and is remarkably tolerant of blur — a blurred
image measured during development scored a comfortable 10.8 dB PSNR while its
SSIM collapsed to 0.013. If you read one number, read SSIM.

---

## Honest limitations

Worth stating plainly in any deposit record:

**It is an interpolation, not a measurement.** The model reproduces how the
subject looked from positions the camera actually occupied. Between and beyond
those positions it is inferring. Surfaces never photographed are invented.

**Reflective and transmissive materials are not represented physically.**
Mirrors, screens and glass are encoded as whatever made the training images
look correct. A mirror becomes a window into a plausible fake room.

**Moving content is frozen or smeared.** For an installation built around
moving image this is a substantive limitation, not a technicality — and the
choice made at capture time (screens off, paused, or playing) should be
recorded in the manifest as the interpretive decision it is.

**Scale is arbitrary unless separately established.** COLMAP recovers geometry
up to an unknown scale factor. If real-world dimensions matter, photograph a
scale bar and record the measurement.

**The format is young.** PLY will remain readable; whether tools to *render*
Gaussian splats persist is a genuine open question. This is the strongest
argument for keeping `originals/` and `sfm/`: from those, the model can be
rebuilt with whatever method exists at the time.

---

## Relationship to standards

This is not a formal OAIS or PREMIS implementation, and does not claim to be.
It follows the same instincts — keep the originals, record provenance and
fixity, document the processing chain, be explicit about what is derived — in a
form small enough to actually be produced by every run rather than assembled by
hand afterwards.

If these packages need to enter a formal repository, `manifest.json` carries
the material a PREMIS record needs: agent, timestamps, software versions,
processing parameters, and fixity for every file.
