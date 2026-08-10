"""Per-camera-group post-mortem for a trained run.

Answers two questions that a single mean PSNR cannot:

1. **Which camera group is dragging the number down?** A mixed capture
   (three phones + Polycam + 4K video) reports one figure over five very
   different sets of views. If one group is badly posed or unresolvable, the
   headline metric describes that failure rather than the reconstruction.

2. **How much of the residual is exposure, not geometry?** Auto-exposure and
   auto-white-balance vary shot to shot, and a splat has one radiance field to
   satisfy all of them. Re-scoring each view after a per-view per-channel
   affine fit (gain + bias, solved in closed form) separates "the model got the
   scene wrong" from "the model got the scene right at a different brightness".

Usage::

    .venv\\Scripts\\python.exe scripts/diagnose_run.py runs/nested-cinema-03-hq
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine import cuda_toolkit  # noqa: E402
from vitrine.colmap_io import Model, read_model  # noqa: E402
from vitrine.dataset import ViewSet  # noqa: E402
from vitrine.losses import psnr, ssim  # noqa: E402
from vitrine.ply import read_splat_ply  # noqa: E402

HOLDOUT_EVERY = 8
#: Scoring resolution. The trainer's own eval fixes this at 1600, which is
#: below what the masters are trained at — a model fit to 2304 or 3072 px
#: detail cannot show that advantage in a 1600 px render. Comparisons between
#: models of different training resolution must be scored at one common
#: resolution, and it should be high enough to see what separates them.
EVAL_LONG_EDGE = int(os.environ.get("VITRINE_EVAL_LONG_EDGE", 2304))
SOURCE_LONG_EDGE = int(os.environ.get("VITRINE_EVAL_LONG_EDGE", 2304))


def affine_corrected_psnr(rendered: torch.Tensor, target: torch.Tensor) -> float:
    """PSNR after the best per-channel gain+bias fit of render to target.

    Least squares per channel: the exposure the model *could* have matched if
    it were free to pick one per view, which is exactly what a per-image
    appearance model would learn.
    """
    corrected = torch.empty_like(rendered)
    for c in range(3):
        x = rendered[..., c].reshape(-1)
        y = target[..., c].reshape(-1)
        a = torch.stack([x, torch.ones_like(x)], dim=1)
        solution = torch.linalg.lstsq(a, y.unsqueeze(1)).solution.squeeze(1)
        corrected[..., c] = (solution[0] * x + solution[1]).reshape(rendered.shape[:-1])
    return psnr(corrected.clamp(0.0, 1.0), target)


def main(run_dir: Path) -> int:
    cuda_toolkit.configure()
    from gsplat import rasterization

    sparse = run_dir / "sfm" / "sparse_text"
    images_dir = run_dir / "ingest" / "images"
    if not images_dir.is_dir():  # runs that reuse another run's staged images
        images_dir = Path(os.environ.get("VITRINE_IMAGES_DIR", images_dir))
    ply_path = run_dir / "model" / "scene.ply"

    model = read_model(sparse)
    ordered = sorted(model.images, key=lambda im: im.name)
    eval_images = [ordered[i] for i in range(0, len(ordered), HOLDOUT_EVERY)]
    print(f"{len(ordered)} registered views; scoring {len(eval_images)} held out")

    subset = Model(
        cameras=model.cameras,
        images=eval_images,
        points_xyz=model.points_xyz,
        points_rgb=model.points_rgb,
    )
    views = ViewSet(subset, images_dir, long_edge=SOURCE_LONG_EDGE, holdout_every=1, device="cuda")

    data = read_splat_ply(ply_path)
    sh_degree = int(data["sh_degree"])
    to = lambda a: torch.from_numpy(np.ascontiguousarray(a)).float().cuda()  # noqa: E731
    means, scales = to(data["means"]), to(data["scales"])
    quats, opacities = to(data["quats"]), to(data["opacities"])
    sh = torch.cat([to(data["sh0"]), to(data["shN"])], dim=1)

    op = torch.sigmoid(opacities)
    print(
        f"{len(means):,} Gaussians  alive(>0.005) {float((op > 0.005).float().mean()) * 100:.1f}%  "
        f"mean opacity {float(op.mean()):.4f}"
    )

    per_group: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    with torch.no_grad():
        for index in range(len(views)):
            batch = views.full(index, max_long_edge=EVAL_LONG_EDGE)
            rendered, _, _ = rasterization(
                means=means,
                quats=F.normalize(quats, dim=-1),
                scales=torch.exp(scales),
                opacities=op,
                colors=sh,
                viewmats=batch.world_to_camera,
                Ks=batch.intrinsics,
                width=batch.width,
                height=batch.height,
                sh_degree=sh_degree,
                packed=True,
                rasterize_mode="antialiased",
            )
            image = rendered[0].clamp(0.0, 1.0)
            group = Path(views.views[index].name).parent.as_posix() or "."
            per_group[group].append(
                (
                    psnr(image, batch.image),
                    float(ssim(image, batch.image)),
                    affine_corrected_psnr(image, batch.image),
                )
            )

    print(f"\n{'group':<24}{'n':>5}{'PSNR':>9}{'SSIM':>9}{'PSNR+exp':>10}{'gain':>7}")
    summary = {}
    all_rows: list[tuple[float, float, float]] = []
    for group, rows in sorted(per_group.items()):
        arr = np.array(rows)
        all_rows.extend(rows)
        summary[group] = {
            "n": len(rows),
            "psnr": round(float(arr[:, 0].mean()), 3),
            "ssim": round(float(arr[:, 1].mean()), 4),
            "psnr_exposure_corrected": round(float(arr[:, 2].mean()), 3),
        }
        print(
            f"{group:<24}{len(rows):>5}{arr[:, 0].mean():>9.2f}{arr[:, 1].mean():>9.4f}"
            f"{arr[:, 2].mean():>10.2f}{arr[:, 2].mean() - arr[:, 0].mean():>7.2f}"
        )
    total = np.array(all_rows)
    print(
        f"{'ALL':<24}{len(all_rows):>5}{total[:, 0].mean():>9.2f}{total[:, 1].mean():>9.4f}"
        f"{total[:, 2].mean():>10.2f}{total[:, 2].mean() - total[:, 0].mean():>7.2f}"
    )
    summary["ALL"] = {
        "n": len(all_rows),
        "psnr": round(float(total[:, 0].mean()), 3),
        "ssim": round(float(total[:, 1].mean()), 4),
        "psnr_exposure_corrected": round(float(total[:, 2].mean()), 3),
    }

    out = run_dir / "model" / "diagnosis.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "runs/nested-cinema-03-hq")))
