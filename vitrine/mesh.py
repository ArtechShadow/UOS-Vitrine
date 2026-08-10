"""Derive a conventional textured mesh from the trained splat.

Why bother, when the splat renders better? Because a mesh is the format an
institutional repository can actually accept, index and open in twenty years.
OBJ and glTF are widely supported and boringly stable; Gaussian splatting is
five years old and still standardising. The mesh is the conservative deposit
copy, and the splat is the good-looking one.

Route: render depth from the training viewpoints, back-project to a coloured
point cloud, and run screened Poisson reconstruction.

The obvious tool for this is Open3D, and it is what most 3DGS meshing code
uses. **It has no Python 3.14 wheel**, and this project runs on 3.14 because
that is the system interpreter. So the fusion step uses ``pymeshlab`` instead —
the same screened Poisson implementation (Kazhdan & Hoppe), just via MeshLab.

Expect the result to be a reasonable *surface*, not survey geometry. Splat
depth is the opacity-weighted mean along each ray, which is well behaved on
solid opaque surfaces and meaningless on glass, mirrors and screens.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def _backproject(
    depth: np.ndarray,      # [H, W]
    colour: np.ndarray,     # [H, W, 3] in [0, 1]
    intrinsics: np.ndarray,  # [3, 3]
    world_to_camera: np.ndarray,  # [4, 4]
    *,
    stride: int = 2,
    min_depth: float = 1e-3,
    max_depth: float = 1e6,
) -> tuple[np.ndarray, np.ndarray]:
    """Lift a depth map into world-space points with colour."""
    height, width = depth.shape
    ys, xs = np.mgrid[0:height:stride, 0:width:stride]
    d = depth[::stride, ::stride]
    c = colour[::stride, ::stride]

    valid = np.isfinite(d) & (d > min_depth) & (d < max_depth)
    if not valid.any():
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)

    xs, ys, d, c = xs[valid], ys[valid], d[valid], c[valid]

    fx, fy = intrinsics[0, 0], intrinsics[1, 1]
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]
    camera_points = np.stack([(xs - cx) / fx * d, (ys - cy) / fy * d, d], axis=1)

    rotation = world_to_camera[:3, :3]
    translation = world_to_camera[:3, 3]
    world_points = (camera_points - translation) @ rotation

    return world_points.astype(np.float32), c.astype(np.float32)


def splat_to_pointcloud(
    ply_path: Path,
    views,
    *,
    indices: list[int] | None = None,
    max_long_edge: int = 1200,
    stride: int = 2,
    depth_percentile: float = 99.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Render depth from each viewpoint and fuse into one coloured cloud.

    ``depth_percentile`` trims the far tail. Rays that miss all geometry come
    back with an enormous or accumulated depth, and left in they scatter points
    across an arbitrarily large volume, which wrecks the Poisson solve.
    """
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

    chosen = indices if indices is not None else list(range(len(views)))
    clouds: list[np.ndarray] = []
    colours: list[np.ndarray] = []

    with torch.no_grad():
        for count, index in enumerate(chosen):
            batch = views.full(index, max_long_edge=max_long_edge)
            rendered, alpha, _ = rasterization(
                means=means, quats=quats, scales=scales, opacities=opacities,
                colors=sh, viewmats=batch.world_to_camera, Ks=batch.intrinsics,
                width=batch.width, height=batch.height, sh_degree=degree,
                packed=True, rasterize_mode="antialiased",
                # Expected depth alongside colour: the opacity-weighted mean
                # distance along each ray.
                render_mode="RGB+ED",
            )
            image = rendered[0, ..., :3].clamp(0, 1).cpu().numpy()
            depth = rendered[0, ..., 3].cpu().numpy()
            coverage = alpha[0, ..., 0].cpu().numpy()

            # Only trust pixels where enough opacity accumulated; elsewhere the
            # "depth" is an average over near-transparent space.
            depth = np.where(coverage > 0.5, depth, np.nan)

            finite = np.isfinite(depth)
            if finite.any():
                far = np.percentile(depth[finite], depth_percentile)
                points, point_colours = _backproject(
                    depth, image,
                    batch.intrinsics[0].cpu().numpy(),
                    batch.world_to_camera[0].cpu().numpy(),
                    stride=stride, max_depth=float(far),
                )
                if len(points):
                    clouds.append(points)
                    colours.append(point_colours)

            if (count + 1) % 25 == 0:
                logger.info("  depth-fused %d/%d views", count + 1, len(chosen))

    if not clouds:
        raise RuntimeError("no usable depth was rendered — is the model empty?")

    points = np.concatenate(clouds)
    point_colours = np.concatenate(colours)
    logger.info("fused %d views into %s points", len(chosen), f"{len(points):,}")
    return points, point_colours


def poisson_mesh(
    points: np.ndarray,
    colours: np.ndarray,
    out_path: Path,
    *,
    depth: int = 10,
    target_points: int = 2_000_000,
    keep_fraction: float = 0.12,
) -> Path:
    """Screened Poisson reconstruction via pymeshlab, written to ``out_path``.

    ``keep_fraction`` trims the lowest-density vertices afterwards. Poisson
    always returns a closed watertight surface, which means it happily
    hallucinates a shell across regions with no data — over an open doorway,
    say. Removing the least-supported vertices cuts most of that away.
    """
    try:
        import pymeshlab
    except ImportError as exc:
        raise RuntimeError(
            "pymeshlab is required for meshing (Open3D has no Python 3.14 wheel). "
            "Install it with: pip install pymeshlab"
        ) from exc

    if len(points) > target_points:
        rng = np.random.default_rng(0)
        keep = rng.choice(len(points), size=target_points, replace=False)
        points, colours = points[keep], colours[keep]
        logger.info("subsampled to %s points for reconstruction", f"{target_points:,}")

    alpha = np.ones((len(colours), 1), dtype=np.float64)
    mesh_set = pymeshlab.MeshSet()
    mesh_set.add_mesh(
        pymeshlab.Mesh(
            vertex_matrix=points.astype(np.float64),
            v_color_matrix=np.hstack([colours.astype(np.float64), alpha]),
        ),
        "fused",
    )

    logger.info("estimating normals")
    mesh_set.compute_normal_for_point_clouds(k=16, smoothiter=2)

    logger.info("screened Poisson reconstruction (depth=%d)", depth)
    mesh_set.generate_surface_reconstruction_screened_poisson(depth=depth, preclean=True)

    if keep_fraction > 0:
        try:
            mesh_set.compute_scalar_by_volumetric_obscurance()
        except Exception:  # noqa: BLE001 - filter names shift between versions
            pass
        try:
            mesh_set.compute_selection_by_condition_per_vertex(
                condselect=f"q < {keep_fraction}"
            )
            mesh_set.meshing_remove_selected_vertices()
            logger.info("trimmed low-confidence vertices below quality %.2f", keep_fraction)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not trim low-density vertices: %s", exc)

    mesh_set.meshing_remove_unreferenced_vertices()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mesh_set.save_current_mesh(str(out_path), save_vertex_color=True)

    measures = mesh_set.get_geometric_measures()
    logger.info(
        "wrote %s — %s vertices, %s faces, %.1f MB",
        out_path.name,
        f"{mesh_set.current_mesh().vertex_number():,}",
        f"{mesh_set.current_mesh().face_number():,}",
        out_path.stat().st_size / 1e6,
    )
    _ = measures
    return out_path


def build_mesh(
    ply_path: Path,
    views,
    out_path: Path,
    *,
    max_views: int = 120,
    depth: int = 10,
) -> Path:
    """Splat to mesh, end to end."""
    step = max(1, len(views) // max_views)
    indices = list(range(0, len(views), step))
    points, colours = splat_to_pointcloud(ply_path, views, indices=indices)
    return poisson_mesh(points, colours, out_path, depth=depth)
