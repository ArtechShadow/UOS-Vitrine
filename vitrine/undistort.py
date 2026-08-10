"""Remove lens distortion from training views.

The rasteriser is a **pinhole** projector: it maps a Gaussian to a pixel with
``K`` alone. COLMAP, meanwhile, solves for a distorted camera — every camera in
this project's captures comes back as ``OPENCV`` with a real radial term
(k1 ~= 0.06, k2 ~= -0.075 on the iPhone groups). Training a pinhole renderer
against distorted photographs asks the optimiser to reconcile two different
cameras, and it does so the only way it can: by blurring.

The error is small in the centre and worst where the detail usually is. At the
corner of a 2304 px working image those coefficients displace a feature by
**10-15 px** — far more than the sub-pixel accuracy fine texture needs. It is
invisible in any single metric but it puts a ceiling on every run.

Correction is a resampling, done once per view at load:

    for each *output* pixel  ->  normalised (x, y)
    apply the distortion model -> (x_d, y_d)
    sample the original image at  (fx*x_d + cx, fy*y_d + cy)

Note the direction: the model maps ideal to distorted, which is exactly what a
backward warp needs, so no iterative inversion is involved.

Undistorting pushes the image corners *outside* the source, so the result is
cropped to the largest rectangle that is entirely valid and ``cx, cy`` moved to
match. That costs a ~1% border and keeps every remaining pixel honest, rather
than replicating edge pixels into a region the optimiser would then try to fit.
"""

from __future__ import annotations

import logging

import numpy as np
import torch

from .colmap_io import Camera

logger = logging.getLogger(__name__)

#: Distortion models this module implements. Fisheye models use a different
#: projection and are refused rather than silently mis-corrected.
_SUPPORTED = {"SIMPLE_RADIAL", "RADIAL", "OPENCV", "FULL_OPENCV", "SIMPLE_PINHOLE", "PINHOLE"}

#: Never inset more than this fraction of a side when trimming invalid border.
#: A correction needing more is a sign the model is wrong, not the image.
MAX_INSET_FRACTION = 0.12


def _distort(x: torch.Tensor, y: torch.Tensor, p: dict[str, float]) -> tuple[torch.Tensor, torch.Tensor]:
    """Apply COLMAP's OPENCV/RADIAL distortion to normalised image coordinates."""
    k1 = p.get("k1", 0.0)
    k2 = p.get("k2", 0.0)
    k3 = p.get("k3", 0.0)
    p1 = p.get("p1", 0.0)
    p2 = p.get("p2", 0.0)

    r2 = x * x + y * y
    radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
    x_d = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
    y_d = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
    return x_d, y_d


def _valid_inset(valid: torch.Tensor) -> tuple[int, int, int, int]:
    """Largest axis-aligned rectangle of ``valid`` pixels, as (top, bottom, left, right) insets.

    Grown one side at a time from the side that is currently worst, which for a
    radial warp converges in a handful of iterations.
    """
    height, width = valid.shape
    top, bottom, left, right = 0, 0, 0, 0
    max_v = int(height * MAX_INSET_FRACTION)
    max_h = int(width * MAX_INSET_FRACTION)

    for _ in range(max_v + max_h + 4):
        window = valid[top : height - bottom, left : width - right]
        if window.numel() == 0 or bool(window.all()):
            break
        # Count invalid pixels on each edge of the current window and trim the
        # worst offender; ties broken toward the vertical, which is arbitrary
        # and does not matter once the loop converges.
        counts = {
            "top": int((~window[0, :]).sum()),
            "bottom": int((~window[-1, :]).sum()),
            "left": int((~window[:, 0]).sum()),
            "right": int((~window[:, -1]).sum()),
        }
        if not any(counts.values()):
            # Invalid pixels only in the interior — nothing a rectangle can fix.
            break
        worst = max(counts, key=lambda k: counts[k])
        if worst == "top" and top < max_v:
            top += 1
        elif worst == "bottom" and bottom < max_v:
            bottom += 1
        elif worst == "left" and left < max_h:
            left += 1
        elif worst == "right" and right < max_h:
            right += 1
        else:
            break
    return top, bottom, left, right


def undistort(
    image: torch.Tensor,
    intrinsics: torch.Tensor,
    camera: Camera,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Undistort one [H, W, 3] view. Returns the corrected image and its new K.

    ``intrinsics`` must already be scaled to ``image``'s resolution; distortion
    coefficients are in normalised coordinates and so need no rescaling.
    Cameras with no distortion (or an unsupported model) are returned
    untouched.
    """
    if camera.model not in _SUPPORTED:
        logger.warning(
            "camera %d model %s has no undistortion implementation — training on distorted views",
            camera.id, camera.model,
        )
        return image, intrinsics
    if not camera.has_distortion:
        return image, intrinsics

    height, width = image.shape[0], image.shape[1]
    fx = float(intrinsics[0, 0])
    fy = float(intrinsics[1, 1])
    cx = float(intrinsics[0, 2])
    cy = float(intrinsics[1, 2])

    ys, xs = torch.meshgrid(
        torch.arange(height, dtype=torch.float32),
        torch.arange(width, dtype=torch.float32),
        indexing="ij",
    )
    x_n = (xs - cx) / fx
    y_n = (ys - cy) / fy
    x_d, y_d = _distort(x_n, y_n, camera.params)
    src_x = x_d * fx + cx
    src_y = y_d * fy + cy

    valid = (src_x >= 0) & (src_x <= width - 1) & (src_y >= 0) & (src_y <= height - 1)

    # grid_sample wants normalised [-1, 1] coordinates over the source.
    grid = torch.stack(
        [2.0 * src_x / max(width - 1, 1) - 1.0, 2.0 * src_y / max(height - 1, 1) - 1.0],
        dim=-1,
    ).unsqueeze(0)
    sampled = torch.nn.functional.grid_sample(
        image.permute(2, 0, 1).unsqueeze(0),
        grid,
        mode="bilinear",
        padding_mode="border",
        align_corners=True,
    ).squeeze(0).permute(1, 2, 0)

    top, bottom, left, right = _valid_inset(valid)
    if top or bottom or left or right:
        sampled = sampled[top : height - bottom, left : width - right, :]

    k = intrinsics.clone()
    k[0, 2] = cx - left
    k[1, 2] = cy - top
    return sampled.contiguous(), k


def describe(camera: Camera, width: int, height: int, intrinsics: torch.Tensor) -> str:
    """Worst-case pixel displacement this camera's distortion causes, for logging."""
    fx = float(intrinsics[0, 0])
    fy = float(intrinsics[1, 1])
    cx = float(intrinsics[0, 2])
    cy = float(intrinsics[1, 2])
    corners = torch.tensor(
        [[0.0, 0.0], [width - 1.0, 0.0], [0.0, height - 1.0], [width - 1.0, height - 1.0]]
    )
    x_n = (corners[:, 0] - cx) / fx
    y_n = (corners[:, 1] - cy) / fy
    x_d, y_d = _distort(x_n, y_n, camera.params)
    shift = torch.sqrt(((x_d - x_n) * fx) ** 2 + ((y_d - y_n) * fy) ** 2)
    return f"cam{camera.id} {camera.model}: up to {float(shift.max()):.1f} px corner displacement"
