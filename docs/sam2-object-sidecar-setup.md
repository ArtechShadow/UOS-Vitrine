# Local SAM2 object-sidecar setup

This machine now has a working alternative to gated SAM3.1: GroundingDINO
SwinT detection, SAM2.1 tiny segmentation, and the external Vitrine sidecar's
multiview Gaussian carving. It produces observed Gaussian **candidates**, not
textured meshes. This alternative is not claimed equivalent to SAM3.1.

## Installed components

| Component | Local location / version |
| --- | --- |
| Sidecar | `tmp/external/vitrine-object-sidecar`, commit `8011b32a57d183dd40af38fcf7bfcf952167603d` |
| SAM2 | `tmp/external/sam2`, commit `2b90b9f5ceec907a1c18123530e92e794ad901a4` |
| Isolated interpreter | Sidecar `.venv/Scripts/python.exe`, Python 3.11 |
| GPU libraries | Inherited read-only from `miniconda3/envs/vitrine-train`: Torch 2.11.0+cu128, torchvision 0.26.0+cu128 |
| Added dependencies | pillow-heif 1.6.0, groundingdino-py 0.4.0, transformers 4.46.3, SAM-2 1.0 |
| Numerical pins | NumPy 1.26.4, OpenCV 4.11.0.86 |
| External runner | Sidecar `run_sam2_local.py` |
| Public model cache | Sidecar `models/` and `models/hf/` |

The external adapter is a **local installed file**, not included in a fresh
checkout of the MIT repository or upstream sidecar. Keep this installed
directory for the presentation. A portable installer is not yet packaged.
The tracked `scripts/run_sam2_sidecar.py` launches that external process;
`scripts/import_sidecar_splats.py` only handles files and core export/validation.
Neither imports the external models or sidecar.

Model sources are the official [SAM2 repository](https://github.com/facebookresearch/sam2)
and [GroundingDINO repository](https://github.com/IDEA-Research/GroundingDINO).
SAM2's documented optional CUDA extension was disabled during installation;
model inference still uses CUDA. First-use downloads are complete. The runner
uses Hugging Face offline mode when its BERT cache exists.

## Repeat on the real master

Run from the repository root, using a new output directory each time:

```powershell
.venv/Scripts/python.exe scripts/run_sam2_sidecar.py `
  --sidecar-root tmp/external/vitrine-object-sidecar `
  --package C:/Users/realg/Desktop/UOS_Vitrine/runs/nested-cinema-04-master `
  --out C:/Users/realg/Desktop/UOS_Vitrine/runs/my-new-object-rehearsal `
  --core-python C:/Users/realg/Desktop/UOS_Vitrine/.venv/Scripts/python.exe `
  --core-importer C:/Users/realg/Desktop/UOS_Vitrine/scripts/import_sidecar_splats.py `
  --max-frames 40 --prompts radio
```

The runner reads `archive/` and resolves additional registered frames from
`ingest/images`. It writes raw masks, rectified inputs and sidecar records under
`pipeline-evidence/`, then launches the core interpreter separately to convert
actual PLY subsets into `.splat` files. The importer verifies source SHA-256,
preserves source records and full-SH PLYs, and validates the canonical manifest.
It refuses to overwrite an existing manifest or candidate directory.
The final camera step uses each candidate's selected real COLMAP source-camera
position and up direction, aiming at that candidate's observed centroid. It
writes `candidate_*/viewer-cameras.json` without changing the geometry; this
avoids the generic rear view that can make a view-dependent splat look smeared.

Rehearsed successfully twice: six candidates, pipeline times 54.53 and 51.74
seconds. These times exclude first-use downloads, model loading and final
core export. Confidence is vote consistency; completeness needs visual review.

## Dashboard integration

Before starting the dashboard, set its executable and shell-free arguments:

```powershell
$env:VITRINE_OBJECT_SIDECAR = 'C:/Users/realg/Desktop/UOS_Vitrine/tmp/external/vitrine-object-sidecar/.venv/Scripts/python.exe'
$env:VITRINE_OBJECT_SIDECAR_ARGS_JSON = ConvertTo-Json -Compress -InputObject @(
  'C:/Users/realg/Desktop/UOS_Vitrine/tmp/external/vitrine-object-sidecar/run_sam2_local.py',
  '--sidecar-root', 'C:/Users/realg/Desktop/UOS_Vitrine/tmp/external/vitrine-object-sidecar',
  '--core-python', 'C:/Users/realg/Desktop/UOS_Vitrine/.venv/Scripts/python.exe',
  '--core-importer', 'C:/Users/realg/Desktop/UOS_Vitrine/scripts/import_sidecar_splats.py',
  '--max-frames', '40', '--prompts', 'radio'
)
.venv/Scripts/python.exe -m vitrine ui
```

The core app appends the selected run and output directory. Do not put a shell
command inside the executable variable. Outputs use exactly one `splat_path`
per candidate with a real SHA-256, neutral candidate labels, source previews
and provenance. Original scene models and preservation archives are untouched.

To check the live output downloads:

```powershell
$env:VITRINE_DEMO_URL = 'http://127.0.0.1:8765'
python -m pytest tests/test_demo_sidecar_live.py -v
```

The tests never start a configured GPU job. A full run is the separate rehearsal
above. New captures need their preservation archive to exist before this
adapter can process them.
