# nested-cinema-04-master — the highest-quality reconstruction to date

**Date:** 2026-08-08
**Hardware:** RTX 5090, Windows 11
**Source:** every usable frame in `source/` — 741 staged, 736 registered

## Result

| | previous best (`03-hq`) | **master (`04`)** |
|---|---|---|
| PSNR, held out | 19.77 | **22.91** |
| SSIM | 0.656 | **0.778** |
| PSNR of the file that ships | 17.13 | **22.91** |
| Live Gaussians | 542k of 2.5M (21.7%) | **1.41M of 2.0M (70.3%)** |
| Median live anisotropy | 541,278 | **52** |
| Training time | 10.7 min | 8.7 min |

The third row matters most. The exported PLY was previously **2.6 dB worse than
the model that had just been scored**, because the export applied a scale
ceiling nobody had measured. That gap is now zero.

## What the source actually contains

`source/` holds five distinct camera groups, and they are not equivalent:

| Group | Staged | Native | Role |
|---|---|---|---|
| `stills` (iPhone 15 Plus, HEIC) | 346 | 5712×3213 | archival master |
| `stills_4032x2268` (iPhone 15 Plus) | 103 | 4032×2268 | archival master |
| `stills_4344x5792` (iPhone 14 Pro) | 72 | 4344×5792 | archival master |
| `polycam_frames` | 100 of 383 | 1024×768 | coverage / SfM glue |
| `video/IMG_6318.MOV` (4K60 HEVC) | 120 of 242 | 3840×2160 | coverage |
| `luma-derivative` | **excluded** | — | derivative of the work being compared against |

`luma-derivative/` is byte-identical to `polycam_frames/` and is in any case a
derivative export; training on it would make the comparison circular.

## The findings

Ranked by what they were worth. Each was measured by changing one thing against
an otherwise identical 736-view, 15k-step run.

### 1. Lens distortion was never corrected — +4.93 dB, +0.155 SSIM

Every camera solves as `OPENCV` with a real radial term. `Camera.has_distortion`
existed and nothing called it, so a pinhole rasteriser had always been fitted to
uncorrected photographs — a 13 px error in the corners of the main stills camera.

This was the dominant bug, and it also explains the previous investigation's
conclusion that large crops cause "opacity collapse": distortion error grows
with distance from the principal point, so a bigger crop takes more of it. With
the correction in place the relationship inverts and larger crops *help*.
Full write-up in `undistortion-finding.md`.

### 2. Frame coverage, not resolution, is what bounds training — the collapse mechanism

A Gaussian receives reconstruction gradient only on steps where it falls inside
the sampled crop, but `OPACITY_REG` and `SCALE_REG` pull on every Gaussian on
every step. The fraction of a frame a crop covers therefore *is* the ratio of
signal to regularisation:

| source | crop | coverage | PSNR | alive | median anisotropy |
|---|---|---|---|---|---|
| 1600 | 1024 | 64% | 22.71 | 68% | 50 |
| 2304 | 1536 | 67% | 22.82 | 70% | 56 |
| 3072 | 1536 | 45% | 12.52 | 3% | 2 |
| 2304 | 1024 | 35% | 10.51 | 3% | 2 |
| 3072 | 1024 | 11% | 13.36 | 25% | 100 |

Raising `source_long_edge` while holding `crop` fixed silently lowers coverage,
which is why "train at higher resolution" looked like it broke training. It does
not break it, it starves it. `train.py` now computes coverage and warns below
50%.

### 3. The export scale ceiling was throwing away most of the run — +3.2 dB

`MAX_SCALE_FRACTION` clipped any Gaussian larger than 5% of the scene scale, at
export, unmeasured. Priced against held-out views:

| ceiling | PSNR | SSIM | Gaussians touched |
|---|---|---|---|
| 0.02 | 13.88 | 0.625 | 7.16% |
| 0.05 | 18.21 | 0.694 | 1.54% ← previous default |
| 0.10 | 19.96 | 0.766 | 0.44% |
| 0.25 | 21.36 | 0.778 | 0.08% ← now |
| none | 21.84 | 0.778 | 0 |

A wall, a ceiling or a dim background genuinely is one large smooth surface.
0.25 still bounds any splat to a quarter of the scene, which is all the guard
was ever for.

### 4. Needle Gaussians — +1.1 dB exported

The Luma reference PLY has a median live axis ratio of **12**. This project's
models had **2,619** (`nested-cinema-01`) to **541,278** (`03-hq`) — splats with
no measurable thickness, which fit the training views and fall apart between
them. Nothing in the objective bounds a Gaussian's minimum thickness: flattening
the axis normal to a surface costs no photometric error and earns a `SCALE_REG`
refund.

Two things fixed it. Most of it was a *symptom* of finding 1 — with distortion
corrected the median falls from 541,278 to 203 on its own, because the needles
were how the optimiser had been absorbing the mismatch. A hard ratio guard at
100 takes the rest.

The guard's direction had to be corrected too: the existing implementation
anchored to the *smallest* axis and shrank the others, which takes the runaway
value as truth and destroys the footprint the photometric loss asked for. It now
anchors to the largest and raises the thin axes to a floor.

### 5. Distant junk Gaussians wrecked the web export — free to remove

The trained model keeps a tail of splats far outside the captured volume:
COLMAP outliers never pruned, and MCMC relocations that flew off. They reach
**197,757 world units** from the centre of a capture whose cameras span 8.4,
and 4% of live Gaussians sit beyond 92.

Most web viewers frame a scene from its bounding box, so a single splat that far
out reduces the room to a dot — the exported `.splat` had a bounding box of
±166,073. Priced against 24 held-out views, removing them costs nothing
measurable:

| cull radius | live kept | live dropped | PSNR | SSIM |
|---|---|---|---|---|
| 3x scene scale | 1,259,718 | 10.46% | 22.66 | 0.7649 |
| 10x scene scale | 1,350,241 | 4.03% | 22.65 | 0.7649 |
| 20x scene scale | 1,377,438 | 2.09% | 22.65 | 0.7649 |
| none | 1,406,903 | 0% | 22.65 | 0.7649 |

Identical to four decimal places across a thirteen-fold range of cull radius.
These Gaussians contribute to no view anyone captured.

`export.write_splat_file` now culls beyond 12x the *median* Gaussian radius —
a robust measure taken from the live population itself, so it needs no COLMAP
model and cannot be defined by the outliers it removes. On this master that
drops 63,046 further Gaussians (4.5% of live) and collapses the exported
bounding box from ±166,073 to **±83**. The master PLY keeps everything.

### 6. Two smaller results

- **Releasing the opacity penalty after densification** — +0.7 dB exported,
  alive 53% → 77%. The penalty drives MCMC's birth-and-death process; past
  `refine_stop` there is no birth left, so it only subtracts.
- **Per-image appearance compensation made things worse** — −1.17 dB. A learned
  per-view gain/bias was implemented on the theory that mixed auto-exposure was
  being charged to geometry (a best-fit affine recovers 1.6 dB on the untreated
  model, so the effect is real). But held-out views are scored at identity, and
  the training views' gains drift the canonical exposure away from them.
  Reverted on the measurement; the code remains behind `APPEARANCE_OPT = False`.

## Resolution: 2304 is the ceiling, and it is not the GPU's fault

With coverage held safe, a 3072-source challenger (crop 2048, 88% coverage)
trained perfectly healthily and still lost:

| | trained at | PSNR @1600 | SSIM @1600 | PSNR @2304 | SSIM @2304 |
|---|---|---|---|---|---|
| master | 2304 / crop 1536 | **22.91** | **0.778** | **22.44** | **0.749** |
| challenger | 3072 / crop 2048 | 22.85 | 0.764 | 22.12 | 0.741 |

Scored at both its own evaluation resolution and a higher one that could show
the extra detail if it were there. It is not: COLMAP solved these poses at long
edge 3200, and beyond ~2304 the limit is pose precision, not pixels. More source
resolution past that point buys sharper *disagreement* between views, which the
optimiser resolves as blur.

The next real gain in sharpness would come from a denser, higher-resolution pose
solve, not from feeding the existing one bigger images.

## Per-group quality, scored at 2304

| Group | n | PSNR | SSIM |
|---|---|---|---|
| `video_IMG_6318` | 15 | 24.59 | 0.886 |
| `stills` | 43 | 22.96 | 0.738 |
| `stills_4032x2268` | 12 | 22.39 | 0.693 |
| `polycam_frames` | 13 | 20.97 | 0.772 |
| `stills_4344x5792` | 9 | **18.55** | **0.611** |
| **all** | 92 | **22.44** | **0.749** |

The iPhone 14 Pro portrait group is the weakest by a wide margin and is the
obvious place to look next — 72 views at a different aspect ratio and focal
length from the other four groups.

A best-fit per-view exposure now recovers only **0.57 dB**, against 1.59 dB on
`03-hq`: the corrected model fits the capture's mixed exposures far better on
its own, which is a large part of why the appearance model stopped paying.

## The recipe

```
source_long_edge  2304        crop            1536   (86% frame coverage)
cap_max           2,000,000   iterations      30,000
sh_degree         3           lr horizon      15,000
undistort         on          appearance_opt  off
max_anisotropy    100         max_scale_frac  0.25
opacity_reg       0.01, scaled by cap, released at refine_stop
strategy          MCMC
```

Reproduce with `scripts/run_nested_cinema_04_master.py`. It reuses the
`nested-cinema-03-hq` pose solve (736/741 registered, 404,570 points, 5 cameras,
~35 min of COLMAP); nothing found here changes the poses.

## A note on comparing against 25.72 dB

`nested-cinema-01` reported 25.72 dB and later work treated it as the number to
beat. It is not comparable to anything here. It was measured on 222 views of a
single session; this is 736 views spanning three phones, a Polycam session and a
4K walkthrough, and its held-out set includes the low-resolution Polycam frames
and motion-blurred video. A harder eval set scores lower at equal quality.

The comparison that *is* valid is 03-hq against 04 — same views, same split,
same eval — and that is +3.1 dB trained, +5.8 dB as exported.

Every number this project has published, including the 25.72 dB baseline, was
measured with the distortion bug active.
