# The lens distortion bug

**Date:** 2026-08-08
**Status:** found, fixed, measured

## What was wrong

Every camera COLMAP solved for this project comes back as `OPENCV` with real
radial distortion:

```
1 OPENCV 1024 768  ... k1= 0.0570 k2=-0.0690   polycam
2 OPENCV 3200 1800 ... k1= 0.0587 k2=-0.0741   iPhone 15 Plus (346 stills)
3 OPENCV 3200 1800 ... k1= 0.0579 k2=-0.0751   iPhone 15 Plus (103 stills)
4 OPENCV 2400 3200 ... k1= 0.0743 k2=-0.1017   iPhone 14 Pro (72 stills)
5 OPENCV 3200 1800 ... k1=-0.0037 k2= 0.0012   4K video
```

`colmap_io.Camera` exposed a `has_distortion` property. **Nothing ever called
it.** The images went to the trainer as shot, and `gsplat.rasterization` is a
pinhole projector — it maps a Gaussian to a pixel through `K` alone, with no
distortion term. The optimiser was being asked to fit a pinhole camera to
photographs taken through a lens that is not one.

At the working resolution the mismatch reaches **13 px in the corners** of the
main stills camera. The centre is fine, which is why it never announced
itself — it is worst exactly where a random crop of a high-resolution source
spends most of its time.

## What it cost

One variable, everything else identical, 736 views, 15k iterations:

| | PSNR | SSIM | PSNR as exported | alive Gaussians |
|---|---|---|---|---|
| undistorted | **19.11** | **0.689** | 17.71 | 42.6% |
| as shot | 14.18 | 0.534 | 9.94 | 6.3% |
| | **+4.93 dB** | **+0.155** | **+7.78 dB** | **+36 pts** |

## What it explains

The 5090 handoff recorded a "catastrophic opacity collapse" whenever `crop`
rose from 768 to 1600, and concluded the crop size was the driver. It was a
symptom. Distortion error grows with distance from the principal point, so a
bigger crop of a bigger source reaches further into the corners and takes more
of the mismatch. Small crops mostly sampled the middle of the frame, where the
lens is nearly pinhole, and looked "healthy" for that reason alone.

The prediction that follows — that with the distortion corrected, larger crops
should stop being harmful and start helping — holds:

| crop | PSNR | SSIM |
|---|---|---|
| 768 | 19.11 | 0.689 |
| 1024 | **20.55** | **0.730** |

The collapse itself has the same root. A Gaussian that cannot be reconciled
with the views it appears in is driven toward zero opacity, and under MCMC a
shrinking live population feeds its own decline. 6.3% alive without the
correction, 42.6% with it, same recipe.

## The fix

`vitrine/undistort.py`, applied once per view at load in `dataset.ViewSet`.
For each *output* pixel it takes the normalised coordinate, applies COLMAP's
distortion model to get the *source* coordinate, and resamples — the model maps
ideal to distorted, which is the direction a backward warp needs, so no
iterative inversion is involved. The result is cropped to the largest fully
valid rectangle (~1% of each edge) and `cx, cy` moved to match, rather than
replicating edge pixels into a region the optimiser would then try to fit.

Fisheye models are refused rather than silently mis-corrected. None of this
capture's cameras use one.

## Consequences for the record

Every quality number this project has published was measured on distorted
input, including the 25.72 dB `nested-cinema-01` laptop baseline that later
work treated as the target to match. That baseline is not wrong as a record of
what the pipeline did, but it is not a ceiling — it was set with this bug
active, and any comparison against it understates what the corrected pipeline
can do.
