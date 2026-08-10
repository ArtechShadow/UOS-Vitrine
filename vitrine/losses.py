"""Differentiable image losses for 3DGS training.

SSIM is the term the reference 3DGS implementation uses alongside L1
(``0.8 * L1 + 0.2 * (1 - SSIM)``) and is the main driver of perceptual
sharpness — it penalises blur that an L1/MSE objective happily tolerates.

Kept in pure PyTorch rather than pulling in ``fused-ssim``: that package needs
its own nvcc JIT pass, and getting gsplat's kernels to build under Python 3.14
is already delicate enough. One fragile compile step is enough.

The Gaussian is applied as two 1D passes rather than one 11x11 kernel. The
square depthwise form measured ~60 ms per forward+backward at a 768x768 crop
on an RTX 3060 (cuDNN picks a poor kernel for ``groups=3``), which would have
added half an hour of pure loss computation to a 30k-iteration run. Separable
is mathematically identical and far cheaper.
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

_WINDOW_SIZE = 11
_SIGMA = 1.5
# Stabilisers from Wang et al. 2004, for data in [0, 1] (dynamic range L = 1).
_C1 = 0.01**2
_C2 = 0.03**2

# Gaussian windows are rebuilt per (device, dtype, channels) at most once.
_window_cache: dict[tuple[torch.device, torch.dtype, int], torch.Tensor] = {}


def _gaussian_window(channels: int, device: torch.device, dtype: torch.dtype) -> torch.Tensor:
    """1D Gaussian kernel shaped for a depthwise conv2d, as ``[C,1,1,K]``.

    Transposing to ``[C,1,K,1]`` gives the vertical pass.
    """
    key = (device, dtype, channels)
    cached = _window_cache.get(key)
    if cached is not None:
        return cached

    coords = torch.arange(_WINDOW_SIZE, device=device, dtype=dtype) - _WINDOW_SIZE // 2
    g = torch.exp(-(coords**2) / (2 * _SIGMA**2))
    g = g / g.sum()
    window = g.view(1, 1, 1, _WINDOW_SIZE).expand(channels, 1, 1, _WINDOW_SIZE).contiguous()

    _window_cache[key] = window
    return window


def _blur(x: torch.Tensor, window: torch.Tensor, channels: int) -> torch.Tensor:
    """Gaussian blur as two 1D depthwise passes.

    Input is padded once by the caller, so the two 'valid' passes together
    return a map the same size as the original image.
    """
    x = F.conv2d(x, window, groups=channels)
    return F.conv2d(x, window.transpose(2, 3), groups=channels)


def _to_nchw(img: torch.Tensor) -> torch.Tensor:
    """Accept ``[H,W,C]`` (the trainer's layout) or ``[N,C,H,W]``; return ``[N,C,H,W]``."""
    if img.dim() == 3:
        return img.permute(2, 0, 1).unsqueeze(0)
    if img.dim() == 4:
        return img
    raise ValueError(f"expected a [H,W,C] or [N,C,H,W] tensor, got shape {tuple(img.shape)}")


def ssim(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    """Mean SSIM over the image, differentiable w.r.t. ``pred``.

    Images are expected in [0, 1]. Returns a scalar in roughly [-1, 1] where 1
    is a perfect match.
    """
    x = _to_nchw(pred)
    y = _to_nchw(target)
    if x.shape != y.shape:
        raise ValueError(f"shape mismatch: {tuple(x.shape)} vs {tuple(y.shape)}")

    channels = x.shape[1]
    window = _gaussian_window(channels, x.device, x.dtype)
    pad = _WINDOW_SIZE // 2

    # Reflect padding rather than zero padding: we train on random crops, and
    # zero padding would darken every crop border and bias the loss there.
    x_p = F.pad(x, (pad, pad, pad, pad), mode="reflect")
    y_p = F.pad(y, (pad, pad, pad, pad), mode="reflect")

    mu_x = _blur(x_p, window, channels)
    mu_y = _blur(y_p, window, channels)

    mu_x_sq = mu_x * mu_x
    mu_y_sq = mu_y * mu_y
    mu_xy = mu_x * mu_y

    sigma_x_sq = _blur(x_p * x_p, window, channels) - mu_x_sq
    sigma_y_sq = _blur(y_p * y_p, window, channels) - mu_y_sq
    sigma_xy = _blur(x_p * y_p, window, channels) - mu_xy

    numerator = (2 * mu_xy + _C1) * (2 * sigma_xy + _C2)
    denominator = (mu_x_sq + mu_y_sq + _C1) * (sigma_x_sq + sigma_y_sq + _C2)

    return (numerator / denominator).mean()


def photometric_loss(
    pred: torch.Tensor,
    target: torch.Tensor,
    ssim_weight: float = 0.2,
) -> tuple[torch.Tensor, float, float]:
    """Reference 3DGS objective: ``(1-w) * L1 + w * (1 - SSIM)``.

    Returns ``(loss, l1_value, ssim_value)`` so callers can log the parts
    without a second forward pass.
    """
    l1 = F.l1_loss(pred, target)
    s = ssim(pred, target)
    loss = (1.0 - ssim_weight) * l1 + ssim_weight * (1.0 - s)
    return loss, float(l1.detach()), float(s.detach())


def psnr(pred: torch.Tensor, target: torch.Tensor) -> float:
    """PSNR in dB for images in [0, 1]."""
    mse = F.mse_loss(pred, target).clamp_min(1e-12)
    return float(10.0 * torch.log10(1.0 / mse))
