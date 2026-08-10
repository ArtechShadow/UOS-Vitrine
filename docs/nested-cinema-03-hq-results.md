# nested-cinema-03-hq — expanded source reconstruction

**Date:** 2026-08-08  
**Run root:** `runs/nested-cinema-03-hq/`  
**GPU:** RTX 5090

## Source preparation

| Group | Count used | Native | Role |
|---|---|---|---|
| stills (iPhone 15 Plus HEIC → staged) | 346 | 5712×3213 | Master |
| stills_4032x2268 (iPhone 15) | 103 | 4032×2268 | Master |
| stills_4344x5792 (iPhone 14 Pro JPEG) | 72 | 4344×5792 | Master |
| polycam_frames | **100** sharpest of 383 | 1024×768 | Coverage |
| video_IMG_6318 (4K60 HEVC) | **120** sharpest of 242 @4fps | 3840×2160 | Coverage |
| luma-derivative | **excluded** | — | Soft cloud keyframes |

Moved numeric `stills/*.jpg` (Polycam IDs) → `source/polycam_frames/`.  
HEIC enabled via `pillow-heif` in `vitrine/ingest.py`.

## SfM (success)

| Metric | Value |
|---|---|
| Registered | **736 / 741** (99.3%) |
| Cameras | **5** (one per group) |
| Sparse points | **404,570** |
| COLMAP GPU | yes |
| Wall time | ~35 min |

This is the strongest Nested Cinema pose solve in the project (previous ~222 views / ~111k points).

## Training attempts

| Run | Recipe | PSNR | SSIM | Alive % | Live GS (export) | Notes |
|---|---|---|---|---|---|---|
| **nested-cinema-03-hq** | crop 1024 / src 2304 / cap 2.5M / 30k / op_reg 0.002 / MCMC | **19.77** | 0.656 | 21.7% | **556k** | **Best of this capture** |
| nested-cinema-03-hq-b | crop 768 / src 2048 / cap 1.5M / 20k / op_reg 0.01 / MCMC | 13.48 | 0.551 | 4.8% | 92k | Collapse after densify stop |
| nested-cinema-03-stills | stills-only train, crop 768 / 20k / op_reg 0.005 | 13.90 | 0.558 | 8.4% | 123k | Same collapse mode |
| nested-cinema-03-hq-c | default strategy, op_reg 0 | — | — | — | — | **Killed** (unbounded densify → 12M+ GS) |

### Failure mode (all MCMC runs)

- During densification window (≤ ~11.2k steps): alive often **90–99%**.
- After densification stops: alive falls rapidly; opacity mean drops.
- Checkpoint PLY often **clamps >90% of Gaussians** on world-unit scale ceiling (scale explosion).
- Not the old crop=1600 opacity collapse alone — denser 404k-point init + multi-res mix also contributes.

### Comparison to prior Nested Cinema

| Run | Data | PSNR | Note |
|---|---|---|---|
| nested-cinema-01 | 222 views, 72 JPEG + video | **25.72** | Laptop archive quality reference |
| nested-cinema-01-5090-control | same recipe on 5090 | **25.06** | Healthy workstation match |
| nested-cinema-02 | **video-only** 720p | 31.5 | Inflated (eval on soft video) |
| **nested-cinema-03-hq** | full expanded source | **19.77** | Best new capture; not yet at 25 dB |

## Artefacts to open

```text
runs/nested-cinema-03-hq/
  recipe.json
  ingest/ingest.json
  sfm/sfm.json + sparse_text/
  model/scene.ply          # 620 MB master
  model/scene.splat        # 17.8 MB web (~556k GS)
  model/train.json
  model/alive_stats.json
  logs/vitrine.log
```

UI: http://127.0.0.1:8765/ → **nested-cinema-03-hq**

## Recommended next experiments

1. **Subsample COLMAP init** to ~100–150k points (or lower `CAP_MAX_POINT_MULTIPLIER`) so MCMC does not start over-dense.
2. **Keep densification open longer** (tie `refine_stop` to full `iterations`, not 75% of LR horizon).
3. **Stills-only SfM** (no polycam) then train laptop-standard recipe — isolate whether polycam poses hurt stills.
4. **Stop export at last high-alive checkpoint** (~10–11k steps) if mid-run PSNR/SSIM are acceptable.
5. Do **not** use `DefaultStrategy` without a hard cap — it ignored `cap_max` and exploded.

## Code / source side effects

- `vitrine/ingest.py`: HEIC/HEIF via pillow-heif  
- `requirements.txt`: `pillow-heif>=0.16`  
- `source/polycam_frames/`: 383 frames moved from `stills/`  
- `scripts/run_nested_cinema_03_hq.py`: pipeline recipe script  
