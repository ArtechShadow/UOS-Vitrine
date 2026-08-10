"""Measure a trained splat against held-out photographs.

The aggregate PSNR/SSIM that training reports is a single number over every
held-out view, and that number can mislead badly when a capture mixes sources.

Concretely: a capture of 72 sharp stills plus 150 handheld video frames has an
eval split dominated by video, and handheld video in a dim room is
motion-blurred. The model renders sharp; the ground truth is smeared. SSIM
punishes exactly that mismatch — a blurred image scores 0.013 against its own
sharp original — so the aggregate collapses and, worse, *stops responding to
genuine improvement*. Training looks stalled when it is not.

Reporting per camera group fixes the diagnosis. The stills are the archival
master and the meaningful benchmark; the video's job is coverage, and it should
be judged separately or not at all.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .losses import psnr, ssim

logger = logging.getLogger(__name__)


@dataclass
class ViewMetrics:
    name: str
    group: str
    psnr: float
    ssim: float
    #: Laplacian variance of the ground-truth frame. Low values mean the
    #: reference itself is soft, which caps the achievable score.
    reference_sharpness: float


@dataclass
class EvaluationReport:
    overall_psnr: float
    overall_ssim: float
    by_group: dict[str, dict[str, float]]
    views: list[dict]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def summary(self) -> str:
        lines = [f"overall: PSNR {self.overall_psnr:.2f} dB  SSIM {self.overall_ssim:.4f}", ""]
        lines.append(f"{'group':<24}{'n':>4}{'PSNR':>9}{'SSIM':>9}{'ref sharpness':>15}")
        lines.append("-" * 61)
        for group, stats in sorted(self.by_group.items()):
            lines.append(
                f"{group:<24}{int(stats['count']):>4}{stats['psnr']:>9.2f}"
                f"{stats['ssim']:>9.4f}{stats['reference_sharpness']:>15.0f}"
            )
        return "\n".join(lines)


def _group_of(name: str) -> str:
    """Camera group from a COLMAP image name like ``stills/IMG_6319.jpg``."""
    parts = Path(name).parts
    return parts[0] if len(parts) > 1 else "ungrouped"


def _sharpness(image: np.ndarray) -> float:
    """Laplacian variance of a [H,W,3] float image in [0,1]."""
    grey = image.mean(axis=2)
    laplacian = (
        -4 * grey[1:-1, 1:-1]
        + grey[:-2, 1:-1] + grey[2:, 1:-1]
        + grey[1:-1, :-2] + grey[1:-1, 2:]
    )
    return float(laplacian.var() * 255**2)


def evaluate_ply(
    ply_path: Path,
    views,
    *,
    indices: list[int] | None = None,
    max_long_edge: int = 1600,
) -> EvaluationReport:
    """Render each view from a saved PLY and measure it against the photograph."""
    import torch

    from . import cuda_toolkit
    from .ply import read_splat_ply

    cuda_toolkit.configure()
    from gsplat import rasterization

    data = read_splat_ply(Path(ply_path))
    device = views.device

    def tensor(name: str) -> torch.Tensor:
        return torch.from_numpy(np.ascontiguousarray(data[name])).float().to(device)

    means = tensor("means")
    scales = torch.exp(tensor("scales"))
    quats = torch.nn.functional.normalize(tensor("quats"), dim=-1)
    opacities = torch.sigmoid(tensor("opacities"))
    sh = torch.cat([tensor("sh0"), tensor("shN")], dim=1)
    degree = int(data["sh_degree"])

    chosen = indices if indices is not None else views.eval_indices
    results: list[ViewMetrics] = []

    with torch.no_grad():
        for index in chosen:
            batch = views.full(index, max_long_edge=max_long_edge)
            rendered, _, _ = rasterization(
                means=means, quats=quats, scales=scales, opacities=opacities,
                colors=sh, viewmats=batch.world_to_camera, Ks=batch.intrinsics,
                width=batch.width, height=batch.height, sh_degree=degree,
                packed=True, rasterize_mode="antialiased",
            )
            image = rendered[0].clamp(0, 1)
            name = views.views[index].name
            results.append(
                ViewMetrics(
                    name=name,
                    group=_group_of(name),
                    psnr=psnr(image, batch.image),
                    ssim=float(ssim(image, batch.image)),
                    reference_sharpness=_sharpness(batch.image.cpu().numpy()),
                )
            )

    grouped: dict[str, list[ViewMetrics]] = defaultdict(list)
    for item in results:
        grouped[item.group].append(item)

    by_group = {
        group: {
            "count": len(items),
            "psnr": float(np.mean([i.psnr for i in items])),
            "ssim": float(np.mean([i.ssim for i in items])),
            "reference_sharpness": float(np.mean([i.reference_sharpness for i in items])),
        }
        for group, items in grouped.items()
    }

    report = EvaluationReport(
        overall_psnr=float(np.mean([r.psnr for r in results])),
        overall_ssim=float(np.mean([r.ssim for r in results])),
        by_group=by_group,
        views=[asdict(r) for r in results],
    )
    return report
