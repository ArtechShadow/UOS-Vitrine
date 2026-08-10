"""Price a distance-based cull against held-out views.

A trained model keeps Gaussians that drifted far outside the captured volume:
COLMAP outlier points that were never pruned, and MCMC relocations that flew
off. On the master they reach 197,757 world units from the scene centre, on a
capture whose cameras span 8.4 — twenty thousand times the room.

They matter for two reasons. Most web viewers frame a scene from its bounding
box, so a single splat that far out reduces the actual room to a dot; and every
one of them costs bytes and fill rate in a file meant to stream.

The question this answers is whether they are *doing* anything. Some distant
geometry is real — what is visible through a doorway, a window, a reflection —
so the cull is measured against the same held-out views as everything else
rather than assumed to be free.

Usage::

    .venv\\Scripts\\python.exe scripts/price_radius_cull.py <run-dir> [--images dir]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine import cuda_toolkit  # noqa: E402
from vitrine.colmap_io import Model, read_model, scene_scale  # noqa: E402
from vitrine.dataset import ViewSet  # noqa: E402
from vitrine.losses import psnr, ssim  # noqa: E402
from vitrine.ply import read_splat_ply  # noqa: E402

HOLDOUT_EVERY = 8
EVAL_LONG_EDGE = 1600
RADII = (3.0, 5.0, 10.0, 20.0, 50.0, None)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--images", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=24)
    args = parser.parse_args()

    cuda_toolkit.configure()
    from gsplat import rasterization

    run_dir = args.run_dir
    images_dir = args.images or run_dir / "ingest" / "images"
    model = read_model(run_dir / "sfm" / "sparse_text")
    ordered = sorted(model.images, key=lambda im: im.name)
    holdout = [ordered[i] for i in range(0, len(ordered), HOLDOUT_EVERY)]
    step = max(1, len(holdout) // args.limit)
    chosen = holdout[::step][: args.limit]

    subset = Model(cameras=model.cameras, images=chosen,
                   points_xyz=model.points_xyz, points_rgb=model.points_rgb)
    views = ViewSet(subset, images_dir, long_edge=EVAL_LONG_EDGE, holdout_every=1)

    scale = scene_scale(model)
    centre = torch.from_numpy(
        np.median(np.stack([im.camera_centre() for im in model.images]), axis=0)
    ).float().cuda()

    data = read_splat_ply(run_dir / "model" / "scene.ply")
    to = lambda a: torch.from_numpy(np.ascontiguousarray(a)).float().cuda()  # noqa: E731
    means, scales = to(data["means"]), to(data["scales"])
    quats, opacities = to(data["quats"]), to(data["opacities"])
    sh = torch.cat([to(data["sh0"]), to(data["shN"])], dim=1)
    sh_degree = int(data["sh_degree"])

    radius = torch.linalg.norm(means - centre, dim=1)
    alive = torch.sigmoid(opacities) > 0.005

    print(f"scene_scale {scale:.3f}; scoring {len(views)} held-out views\n")
    print(f"{'cull':>12}{'live kept':>14}{'dropped':>10}{'PSNR':>9}{'SSIM':>9}")

    for multiple in RADII:
        keep = torch.ones_like(alive) if multiple is None else radius < multiple * scale
        kept_live = int((keep & alive).sum())
        dropped = 100.0 * (1 - kept_live / int(alive.sum()))

        psnrs, ssims = [], []
        with torch.no_grad():
            for index in range(len(views)):
                batch = views.full(index)
                rendered, _, _ = rasterization(
                    means=means[keep],
                    quats=F.normalize(quats[keep], dim=-1),
                    scales=torch.exp(scales[keep]),
                    opacities=torch.sigmoid(opacities[keep]),
                    colors=sh[keep],
                    viewmats=batch.world_to_camera,
                    Ks=batch.intrinsics,
                    width=batch.width,
                    height=batch.height,
                    sh_degree=sh_degree,
                    packed=True,
                    rasterize_mode="antialiased",
                )
                image = rendered[0].clamp(0.0, 1.0)
                psnrs.append(psnr(image, batch.image))
                ssims.append(float(ssim(image, batch.image)))

        label = "none" if multiple is None else f"{multiple:g}x scale"
        print(
            f"{label:>12}{kept_live:>14,}{dropped:>9.2f}%"
            f"{float(np.mean(psnrs)):>9.2f}{float(np.mean(ssims)):>9.4f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
