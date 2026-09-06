# Object separation demo review — 5 September 2026

**Latest result: the public SAM2.1 alternative produces real isolated Gaussian
candidates on the presentation machine.** Two 40-frame runs produced six
candidates in 54.53 and 51.74 seconds of pipeline time. The second also completed
the separate core exporter and validated all six canonical object records.

Six candidates are published under `runs/nested-cinema-04-master/objects`.
They are observed Gaussian subsets, not generated textured meshes. Labels are
neutral equipment candidates because the `radio` prompt also detects
reel-to-reel equipment. Visual review found a crop-to-carve mismatch and mixed
geometry in candidate 02, and fragmentation in candidate 01. These are **not
six verified correctly isolated objects**.
The history below is superseded by the successful SAM2 result at the end.

## Evidence from the live dashboard

Checked `http://127.0.0.1:8765` using `nested-cinema-04-master`.

| Check | Result |
| --- | --- |
| GET `/api/runs/nested-cinema-04-master/objects` | HTTP 200; `ready=true`, `configured=false`, `running=false`, `outputs=null` |
| Master PLY | 496,001,532 bytes; served successfully; first bytes identify a PLY file |
| Web splat | 51,687,616 bytes; served successfully |
| POST objects endpoint while unconfigured | HTTP 409 with an explicit configuration error; no job launched |
| Existing output inventory | None of the current run directories contains `objects/objects.json` |
| Sidecar configuration | `VITRINE_OBJECT_SIDECAR` absent from inspected process, user and machine environments; live API independently reports unconfigured |
| Repeatable real capture smoke checks | **2 passed, 1 skipped**; skipped check requires actual separated object output |

No source or reference data was changed, no sibling repository was edited, and
no substitute or generated object output was used to make the test pass.

## Runtime and test command

The repository `.venv/Scripts/python.exe` works with normal host access:
an elevated check reported Python 3.11.0 and imported `serve.py` successfully.
The earlier sandboxed launch failed because of access restrictions; that
failure did not establish a missing interpreter or broken environment.
The installed conda environment at
`C:\Users\realg\miniconda3\envs\vitrine-train\python.exe` runs Python 3.11.15
and imports the dashboard successfully, but does not have pytest installed.
System Python 3.14 has pytest 9.0.2 but cannot import `serve.py` because Python
3.14 no longer contains `cgi`. The HTTP smoke tests avoid that import and test
the actual running server instead.

```powershell
$env:VITRINE_DEMO_URL = 'http://127.0.0.1:8765'
python -m pytest tests/test_demo_sidecar_live.py -v
```

These opt-in checks only POST when the server reports an unconfigured sidecar;
they never start a configured GPU sidecar. The output check skips explicitly
when no real separated objects exist. These checks do not replace a successful
end-to-end sidecar rehearsal or the existing backend test suite.

## Changes supporting the UI

- Run summaries now expose the existing archive title and one real source-image
  preview for presentation of captures in the library. The master title and
  preview were checked against its on-disk archive and registered images.
- Composed-scene links now pass the same contained-file check as mesh links,
  avoiding a download link that escapes the run directory.
- Existing sidecar validation and subprocess licensing boundary remain intact.

## Required before showing separation as working

1. Install or locate the real external executable that accepts
   `--package <run-directory> --out <output-directory>`. Set
   `VITRINE_OBJECT_SIDECAR` to that executable before starting the dashboard.
   The UI launch currently accepts one executable via the environment. A
   Python module needing extra arguments requires a proper executable wrapper
   or an explicit extension to the dashboard launch configuration; a shell
   command string in this variable will not work.
2. Run the sidecar on the real master, with the GPU available, and preserve its
   log. Measure elapsed time and inspect the recovered radio/sofa or other real
   installation objects. A process exit code alone is insufficient.
3. Confirm `vitrine/object/1` output and SHA-256 validation complete, that each
   mesh opens with correct orientation and scale, and that confidence/coverage
   are meaningful. Check composed scene output separately if supplied.
4. Rehearse the full UI journey through a successful output and a recoverable
   failed run. Restart the dashboard once to confirm displayed state and
   artifact links survive. Keep prepared, validated outputs available for the
   event; do not label an unconfigured stage as ready.

The UI can honestly show “Object isolation · setup required” now. It should
only show recovered object cards after real validated output exists.

## Follow-up after pulling main at `856dce1`

The newly pulled research report contains genuine **reported results from a
different machine**, plus a concrete module invocation. Appendix D records
object-sidecar commit `6fb288d` and this Linux development command:

```bash
cd object-sidecar && source smoke/env.sh
CUDA_VISIBLE_DEVICES=2 .venv/bin/python -m sidecar run \
  --package ../datasets/nested-cinema-04 \
  --out ../runs/nc04-object-pass2 --max-frames 0
```

`report/sections/04-live-results.tex` reports a first object pass taking 352
seconds on an RTX 6000 Ada. `report/sections/10-appendix.tex` explicitly lists
Windows 11 / RTX 5090 acceptance as **not yet run**. These are research-report
claims, not results reproduced on this presentation machine. The README's
`/path/to/object-sidecar` remains a placeholder, not an installation location.

The documented command requires `-m sidecar run`, environment setup and an
installed module. Pointing the dashboard directly at `python.exe` would omit
those arguments and would not launch this command correctly.

Read-only follow-up checked the current desktop and GitHub project locations,
the two installed conda environments, PATH commands `object-sidecar`/`sidecar`,
and the sibling project directory trees for an object-sidecar installation.
No matching executable or standalone `object-sidecar` directory was located
in those checked locations. This is a bounded search, not proof that no copy
exists elsewhere. No sibling code or environment was changed.

This checkout-discovery finding was superseded when the user supplied the
repository, as recorded below.

## Actual repository and Windows execution

Cloned the user-supplied
`https://github.com/DreamLab-AI/vitrine-object-sidecar` at commit
`8011b32a57d183dd40af38fcf7bfcf952167603d` into
`tmp/external/vitrine-object-sidecar`, which is ignored by the MIT repository.
The external Python module was invoked in its own process; none of its code
was imported or copied into `vitrine/`.

Created an isolated sidecar `.venv` using the existing conda environment's
system packages (Torch 2.11.0+cu128), then installed `pillow-heif 1.6.0`,
`groundingdino-py 0.4.0`, and `transformers 4.46.3` there. NumPy and OpenCV
were pinned to the documented compatible `1.26.4` and `4.11.0.86` versions.
No base training environment was changed and no primary model checkpoint was
downloaded. The first attempt exposed missing HEIC decoding; installing the
decoder resolved that failure.

The real Windows CLI test used:

```powershell
# From tmp/external/vitrine-object-sidecar
.venv/Scripts/python.exe -m sidecar run `
  --package C:/Users/realg/Desktop/UOS_Vitrine/runs/nested-cinema-04-master/archive `
  --out C:/Users/realg/Desktop/UOS_Vitrine/runs/demo-object-sidecar-20260905 `
  --max-frames 5 --prompts radio --detect-max-side 1024
```

**Passed:** parsed the actual 736 registered images, five OPENCV camera models
and 404,570 COLMAP points; produced five real rectified image PNGs (including
HEIC originals) and `rectified/intrinsics.json` in the output directory above.
A separate frame-resolution check with `--extra-originals` pointing at the
run's `ingest/images` resolved all **736/736** registered images, including
the 120 video-camera frames omitted by the archive-only run. Use that input
option for the full rehearsal.

**Failed next:** detector loading stopped at its hardcoded, absent developer
path `C:/mnt/dell/shared/gaussian/LichtFeld-Studio/data/comfyui/models/grounding-dino/GroundingDINO_SwinT_OGC.cfg.py`.
The source also hardcodes Linux locations for the GroundingDINO weights, SAM
repository and SAM3.1 checkpoint; the CLI currently has no model-path flags.

**Model-access prerequisite verified:** official Hugging Face metadata for
`facebook/sam3.1` reports manually gated access. Its required
`sam3.1_multiplex.pt` is **3,502,755,717 bytes** (about 3.50 GB). An HTTP HEAD
request to the checkpoint returned **401**, explicitly requiring approved
access and authentication. No token was present in the inspected standard
token-file location or HF token environment variables. Model metadata and
access were checked without downloading the weights.

The minimal external prerequisite is an authorized SAM3.1 checkpoint and its
matching SAM repository, or approved Hugging Face access to obtain them.
The public GroundingDINO config/weights and auxiliary BERT encoder also need
local paths. The sidecar's smoke report estimates BERT at approximately
440 MB; no detector or encoder checkpoint size was independently measured.

## Integration gaps confirmed from the supplied implementation

1. **Input layout:** `sidecar run` reads a preservation archive with
   `sfm/cameras.txt` and `originals/`. The dashboard currently passes the run
   directory. A launcher must select `run/archive` and add `run/ingest/images`
   as extra originals for complete frame coverage.
2. **CLI:** the launcher must invoke `python -m sidecar run`, not simply pass
   a Python executable as `VITRINE_OBJECT_SIDECAR`.
3. **Manifest:** despite sharing the `vitrine/object/1` schema label, the
   sidecar writes `assets[]`, `placement`, `carve` and `stage_scores`, while
   the core validator requires `object_id`, `mesh_path` and record-level
   `sha256`. A file adapter is required before the core can accept results.
4. **Actual output type:** the default CLI stages are identify, carve, seed,
   emplace; their manifest assets use `role="object_ply"`. These are carved
   Gaussian point assets. They must not be presented as reconstructed textured
   meshes. Generation/mesh stages require additional setup and are not run by
   this default CLI.

No adapter was installed without actual compatible output to validate it
against. No synthetic mesh, mask, manifest or success state was created.
The current result is a successful real-data preprocessing smoke, followed
by a precise model-configuration failure and independently verified checkpoint
access gate. At that point, detection and isolation had not succeeded.

## Successful public-model alternative: SAM2.1

Installed the official [SAM2 repository](https://github.com/facebookresearch/sam2)
at `2b90b9f5ceec907a1c18123530e92e794ad901a4`, with its public SAM2.1 tiny
checkpoint. The optional CUDA postprocessing extension was disabled
(`SAM2_BUILD_CUDA=0`), as supported by its installation instructions. Inference
ran on RTX 5090 using existing Torch 2.11.0+cu128 in the isolated environment.

An independently authored external runner supplies real
[GroundingDINO](https://github.com/IDEA-Research/GroundingDINO) boxes to SAM2's
image predictor. Both models run locally. Repeated execution uses
`HF_HUB_OFFLINE=1` after BERT is cached. No capture is sent for cloud inference.

| Check | Measured result |
| --- | --- |
| Frame coverage | 736/736 resolvable; 40 sampled across all five cameras |
| Real segmented instances | 100 |
| Median mask / box area | 0.719; range 0.228–0.957; representative mask inspected |
| Carved candidates | 6 |
| Carved PLY Gaussian counts | 28,172; 25,023; 22,469; 18,289; 12,668; 4,193 |
| Web splat Gaussian counts | 23,163; 20,835; 17,385; 14,990; 11,360; 3,142 |
| Initial pipeline duration | 54.53 seconds |
| Repeat pipeline duration | 51.74 seconds, excluding model loading and core export |
| Peak identification VRAM | 2,211.4 MiB allocated |
| Core contract | All 6 real records and exported SHA-256 hashes validated |

Web conversion drops Gaussians below the representable opacity floor, with
radius culling disabled. Original full-SH carved PLYs remain beside the web
assets. Confidence measures carve-vote consistency, not calibrated object
identity or completeness. Placement is `init-only`; splats retain observed
world coordinates. Source crops are evidence previews, not mesh renders.

Downloaded checkpoints:

- SAM2.1 tiny: 156,008,466 bytes;
  `7402e0d864fa82708a20fbd15bc84245c2f26dff0eb43a4b5b93452deb34be69`.
- GroundingDINO SwinT: 693,997,677 bytes;
  `3b3ca2563c77c69f651d7bd133e97139c186df06231157a64c507099c52bc799`.
- Public BERT encoder cached at first load. All models were downloaded at
  installation, not bundled in this repository. The external code originally
  labels GroundingDINO `redistributed`; the local runner corrects future
  records to `downloaded-at-install`. First-run raw records are retained.

Full integration rehearsal output is `runs/demo-object-sam2-rehearsal-20260906`.
Its `pipeline-evidence/` contains rectified images, true masks, raw manifests
and stage summaries. Initial evidence remains at
`runs/demo-object-sam2-20260906`. Both are ignored output directories.

See [local setup and repeatable launch](sam2-object-sidecar-setup.md).

After the configured dashboard restart, real HTTP smoke checks passed:
**2 passed, 1 skipped**. The skip is the intentional unconfigured-launch test,
because the sidecar is now configured; actual splat downloads passed for all
six candidates. Per-object source-camera presets were generated and verified
on both output sets, preserving geometry. Final visual acceptance remains a
separate browser review; a source crop alone does not prove 3D completeness.

## Visual acceptance findings — candidates remain experimental

Candidate 02 (`objects/radio_01.ply` in the external output) has a source
preview showing a silver portable boombox. Its real 3D view from the selected
source-camera position instead shows a television plus deck/equipment stack,
with long floating Gaussian shapes around it. This is visible in
`output/playwright/studio-object-radio.png`. The crop is therefore not reliable
evidence of the candidate's identity. Candidate 01 corresponds to reel-to-reel
equipment more recognizably, but its extracted geometry is fragmented.

Candidate 02's source record reports 25,023 carved Gaussians, eight supporting
views, and vote confidence 0.9745. That high consistency score did **not** prevent
the visual mismatch and must not be presented as 97% extraction accuracy.
Its seed is frame `stills_4344x5792/IMG_6355.jpg` (frame ID 586), which contains
five separate `radio` detections in the actual identify record.

Code inspection gives a specific explanation for possible seed misassignment:
the external `_seed_stage` filters detections by candidate supporting **frame
IDs**, then considers every same-label mask within those frames. It does not
retain the candidate's exact detection/mask association within a frame. Thus
another device in the same supporting photograph can win seed selection.
This is a confirmed association gap; it does not alone prove which stage
introduced the additional 3D geometry. The broad prompt, multiview association,
carving and the source splat's elongated Gaussians require separate quality
investigation.

The next quality fix is to carry exact instance/mask identities through carve
to seed selection, check projected candidate geometry against that same mask,
and review mixed/fragmented candidates before presenting them as reusable
assets. No additional GPU run or geometry alteration was performed for this
diagnosis. Runtime success, valid hashes and real masks demonstrate a working
experimental workflow; they do not establish clean object separation.
