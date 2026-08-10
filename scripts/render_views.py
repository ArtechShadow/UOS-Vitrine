"""Render a trained PLY from held-out and deliberately novel viewpoints.

Held-out PSNR is measured on views a few frames from a training camera, so it
rewards a model that memorises the capture path. The failure this project
actually has — needle-thin Gaussians — hides there and appears the moment the
camera moves somewhere nobody stood.

So this renders two things:

``holdout``   ground truth beside the render, at full evaluation resolution.
``novel``     the same views pulled back along their own view axis and rotated
              off-axis, which is where degenerate splats reveal themselves as
              spikes, sheets and popping.

Usage::

    .venv\\Scripts\\python.exe scripts/render_views.py <run-dir> [--ply path] [--out dir]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vitrine import cuda_toolkit  # noqa: E402
from vitrine.colmap_io import Model, read_model  # noqa: E402
from vitrine.dataset import ViewSet  # noqa: E402
from vitrine.ply import read_splat_ply  # noqa: E402

HOLDOUT_EVERY = 8
RENDER_LONG_EDGE = 1400

#: How far to pull the novel camera back, as a fraction of scene scale, and how
#: far to swing it. Big enough to leave the capture path, small enough that the
#: geometry is still meant to be there.
NOVEL_PULLBACK = 0.12
NOVEL_YAW_DEGREES = 12.0


def load_params(ply_path: Path, device: str = "cuda") -> tuple[dict, int]:
    data = read_splat_ply(ply_path)
    to = lambda a: torch.from_numpy(np.ascontiguousarray(a)).float().to(device)  # noqa: E731
    params = {
        "means": to(data["means"]),
        "scales": to(data["scales"]),
        "quats": to(data["quats"]),
        "opacities": to(data["opacities"]),
        "sh": torch.cat([to(data["sh0"]), to(data["shN"])], dim=1),
    }
    return params, int(data["sh_degree"])


def render(params: dict, sh_degree: int, world_to_camera, intrinsics, width: int, height: int):
    from gsplat import rasterization

    rendered, _, _ = rasterization(
        means=params["means"],
        quats=F.normalize(params["quats"], dim=-1),
        scales=torch.exp(params["scales"]),
        opacities=torch.sigmoid(params["opacities"]),
        colors=params["sh"],
        viewmats=world_to_camera,
        Ks=intrinsics,
        width=width,
        height=height,
        sh_degree=sh_degree,
        packed=True,
        rasterize_mode="antialiased",
    )
    return rendered[0].clamp(0.0, 1.0)


def offset_pose(world_to_camera: torch.Tensor, scene_scale: float) -> torch.Tensor:
    """Pull the camera back along its view axis and yaw it off the capture path."""
    w2c = world_to_camera[0].clone()
    yaw = np.deg2rad(NOVEL_YAW_DEGREES)
    rotation = torch.tensor(
        [[np.cos(yaw), 0.0, np.sin(yaw)], [0.0, 1.0, 0.0], [-np.sin(yaw), 0.0, np.cos(yaw)]],
        dtype=w2c.dtype, device=w2c.device,
    )
    # Camera-space transform: rotate about the camera's up axis, then step back
    # along +z (which is *away* from the scene in COLMAP's convention).
    delta = torch.eye(4, dtype=w2c.dtype, device=w2c.device)
    delta[:3, :3] = rotation
    delta[2, 3] = NOVEL_PULLBACK * scene_scale
    return (delta @ w2c).unsqueeze(0)


def save(tensor: torch.Tensor, path: Path) -> None:
    array = (tensor.detach().cpu().numpy() * 255).clip(0, 255).astype(np.uint8)
    Image.fromarray(array).save(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--ply", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--count", type=int, default=4)
    parser.add_argument(
        "--images", type=Path, default=None,
        help="staged image directory, when the run reuses another run's ingest",
    )
    args = parser.parse_args()

    cuda_toolkit.configure()
    run_dir = args.run_dir
    ply_path = args.ply or run_dir / "model" / "scene.ply"
    out_dir = args.out or run_dir / "renders"
    out_dir.mkdir(parents=True, exist_ok=True)
    images_dir = args.images or run_dir / "ingest" / "images"

    model = read_model(run_dir / "sfm" / "sparse_text")
    ordered = sorted(model.images, key=lambda im: im.name)
    holdout = [ordered[i] for i in range(0, len(ordered), HOLDOUT_EVERY)]
    # Spread the sample across camera groups rather than taking a run of
    # neighbouring frames from whichever folder sorts first.
    step = max(1, len(holdout) // args.count)
    chosen = holdout[::step][: args.count]

    subset = Model(
        cameras=model.cameras, images=chosen,
        points_xyz=model.points_xyz, points_rgb=model.points_rgb,
    )
    views = ViewSet(subset, images_dir, long_edge=RENDER_LONG_EDGE, holdout_every=1)
    params, sh_degree = load_params(ply_path)
    print(f"{len(params['means']):,} Gaussians from {ply_path}")

    with torch.no_grad():
        for index in range(len(views)):
            batch = views.full(index)
            name = Path(views.views[index].name).as_posix().replace("/", "_").rsplit(".", 1)[0]

            image = render(
                params, sh_degree, batch.world_to_camera, batch.intrinsics, batch.width, batch.height
            )
            pair = torch.cat([batch.image, image], dim=1)
            save(pair, out_dir / f"{name}_holdout.jpg".replace(".jpg", ".png"))

            novel = render(
                params, sh_degree,
                offset_pose(batch.world_to_camera, views.scene_scale),
                batch.intrinsics, batch.width, batch.height,
            )
            save(novel, out_dir / f"{name}_novel.png")
            print(f"  {name}")

    print(f"wrote {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
