# Video-only quality experiment — 6 September 2026

**Selected result:** `runs/nested-cinema-video-demo-20260906/model/scene.ply` and `scene.splat`.

Disabling per-image appearance compensation improved the actual exported model by **4.440 dB PSNR and 0.005743 SSIM** in a matched test. The pipeline now defaults to appearance compensation off. The existing hardware profiles, crop sampler, loss, SfM algorithm and export ceiling were not changed.

## Source and registration

Only `source/video/IMG_6318.MOV` was used: 630,368,504 bytes, SHA256 `8309fb35e037fbed34bf0154554a080d617996461c6dee68f751afd1fd448f98`. The checksum was rechecked after selecting the output and remained unchanged. No existing master or source artifact was overwritten.

The experiment extracted 242 frames at the existing 4 fps setting, selected 200 using the existing sharpness/coverage selector, and staged them at 3200×1800. It performed a **fresh video-only COLMAP solve**, registering **200/200 images**, one OPENCV camera and **108,865 sparse points**. It did not filter video views out of an older mixed-source model. The solved lens has approximately 5.5 pixels of corner displacement at working resolution; undistortion remained enabled.

## Controlled measurement

Both candidates used the same staged images, pose files, code hashes, seed 0, 175 training views and 25 held-out views. Their saved evaluation splits are byte-identical. The only training setting changed between candidates was appearance compensation.

| Setting | Both candidates |
|---|---|
| Source long edge / crop | 2304 / 1536 |
| Actual video image / sampled patch | 2304×1296 / 1536×1296 |
| Actual frame coverage | 66.7% |
| Requested / effective Gaussian cap | 2,000,000 / 1,632,975 |
| Iterations / LR horizon | 15,000 / 15,000 |
| Strategy / SH degree | MCMC / 3 |
| Maximum anisotropy / export scale fraction | 100 / 0.25 |
| Opacity penalty after densification | Released |
| Evaluation | Saved PLY, all 25 held-out views, long edge 1600 |

The effective cap is the existing 15× sparse-point-count guard, not a newly chosen profile value. The safe crop recipe derives from the previously measured master; this experiment isolates appearance compensation and **does not validate replacing stock workstation profiles**.

| Actual saved PLY | Appearance ON (previous default) | Appearance OFF (selected) | OFF minus ON |
|---|---:|---:|---:|
| PSNR | 26.566763 dB | **31.006875 dB** | **+4.440112 dB** |
| SSIM | 0.94123222 | **0.94697483** | **+0.00574261** |
| Held-out views won by OFF | — | 24/25 PSNR; 18/25 SSIM | — |
| Observed training time | 4.4 minutes | 4.3 minutes | — |

These are independent `evaluate_ply` results for the files on disk, not intermediate sampled training evaluations. This is one seed on one capture, not an estimate of universal improvement or an isolated hardware benchmark. The 25 views were withheld from Gaussian photometric training; all 200 images contributed features to SfM. Nearby video frames are correlated, so these metrics do not establish unseen-side geometry or replace visual inspection.

## Visual evidence

Six held-out views span the complete video sequence: frames 1, 42, 83, 122, 163 and 234. The source images saved beside each candidate were hash-checked for equality.

- `runs/nested-cinema-video-demo-20260906/comparison/source-on-off.png`: source, ON and OFF at identical poses.
- `runs/nested-cinema-video-demo-20260906/comparison/source-on-off-crops.png`: the same comparisons using unscaled 400-pixel centre crops.
- Each experiment's `previews/` retains full-resolution 1600-pixel source and rendered images.

OFF better matches source brightness and colour, particularly the opening wall and final room overview. Newspaper text, fine edges and some peripheral surfaces remain soft in both candidates. This is a measured exposure/fidelity improvement, not a claim that every detail is sharp or every viewpoint is reconstructed.

The selected browser `.splat` has **1,479,299 records**, 47,337,568 bytes. The SH3 PLY has **1,632,975 Gaussians**, 404,979,332 bytes. The browser derivative carries SH0 colour and quantized attributes; the table scores the full SH3 PLY, not browser rendering. Three saved viewpoints were generated from this run's registered poses: frame 234 (Room overview), frame 42 (Sofa and wall), and frame 83 (Audio equipment).

## Pipeline corrections

1. **Appearance compensation now defaults off.** The matched exported-file comparison above supports this change and agrees with the earlier mixed-camera master experiment. The optional appearance implementation remains available for explicit experiments.
2. **Coverage reporting uses actual clipped patch dimensions.** The old square-area calculation reported 79% on these video frames; the real `ViewSet.crop` patch covers 66.7%. The corrected diagnostic averages `min(crop,width) × min(crop,height) / (width × height)` over loaded views. A direct test on registered frame 233 confirmed 2304×1296 input and 1536×1296 output. Sampling and training settings are unchanged.
3. **Any altered export scales trigger export evaluation.** The previous threshold skipped scoring if no more than 0.1% of Gaussians were changed. The ON candidate altered about 0.074%, recorded raw and purported export PSNR both as 26.804, but its actual PLY scored 26.566763. The guard is now `clamped_fraction > 0.0`. This changes reporting only. The historical candidate report is preserved; its canonical `evaluation.json` is authoritative.

There is also a measured remaining export cost. For OFF, the trainer evaluated the raw and scale-clamped tensors on the same full 25-view set at 1600: **32.055 dB raw versus 31.007 dB clamped**, a 1.048 dB loss. The controlled scale change touched approximately 0.18% of Gaussians. The export ceiling remains unchanged for this matched experiment; a different guard needs its own evaluation and visual review. The raw unclamped state was not retained as a separate final artifact, so it cannot be recovered from the clipped PLY alone.

## Runtime and reproducibility

The original project `.venv` ran torch **2.13.0+cu130** on the RTX 5090. Its CUDA extension compiled successfully in approximately 120 seconds; the training runs then used that working extension. Docker Desktop initially failed on stale runtime socket paths. After those runtime endpoints were preserved and repaired, the existing COLMAP container's GPU probe passed. No factory reset or Docker data deletion was used.

Observed timings after Docker recovery: feature extraction 1.16 minutes, exhaustive matching 5.61 minutes, mapping 2.86 minutes, and **10.06 minutes for the complete SfM stage** including bundle adjustment and conversion. Frame extraction/selection/staging took 194.9 seconds; each training view cache loaded in about 29 seconds. These are workstation observations with normal desktop/browser activity, not guaranteed demo timings.

Use a new run directory when reproducing; the script refuses to overwrite experiments:

```powershell
.venv/Scripts/python.exe scripts/run_video_demo_experiment.py prepare --run-dir runs/new-video-comparison --source source/video/IMG_6318.MOV
.venv/Scripts/python.exe scripts/run_video_demo_experiment.py train --run-dir runs/new-video-comparison --appearance off
.venv/Scripts/python.exe scripts/run_video_demo_experiment.py train --run-dir runs/new-video-comparison --appearance on
.venv/Scripts/python.exe scripts/run_video_demo_experiment.py compare --run-dir runs/new-video-comparison
```

`solve` resumes prepared frames after a Docker startup failure. Each candidate retains its recipe, source provenance, pose/code hashes, exact evaluation split, canonical evaluation, training log, PLY/SPLAT hashes and previews. `selection.json` identifies the selected result. Selected output hashes were checked after copying into `model/`. No preservation archive was built for this comparison, and the existing master demo remains separate.

Validation included both full real-data training/evaluation runs, exact split/hash agreement, real crop dimensions, source checksum preservation, visual source/on/off review, Python syntax and diff checks. No synthetic quality fixture or additional training run was used.
