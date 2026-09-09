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
    min_depth: float = 0.0,
    max_depth: float = float("inf"),
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
    if not np.isfinite([fx, fy, cx, cy]).all() or fx <= 0 or fy <= 0:
        raise ValueError("Cannot back-project with invalid camera intrinsics")
    if not np.isfinite(world_to_camera).all():
        raise ValueError("Cannot back-project with non-finite camera pose")
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
    support=None,
    scene_ply_path: Path | None = None,
    return_diagnostics: bool = False,
) -> tuple[np.ndarray, np.ndarray] | tuple[np.ndarray, np.ndarray, dict]:
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

    def prepare(source_data):
        def source_tensor(name: str) -> torch.Tensor:
            return torch.from_numpy(np.ascontiguousarray(source_data[name])).float().to(device)

        return {
            "means": source_tensor("means"),
            "scales": torch.exp(source_tensor("scales")),
            "quats": torch.nn.functional.normalize(source_tensor("quats"), dim=-1),
            "opacities": torch.sigmoid(source_tensor("opacities")),
            "sh": torch.cat([source_tensor("sh0"), source_tensor("shN")], dim=1),
            "degree": int(source_data["sh_degree"]),
        }

    from .object_support import (
        ObjectSupportError,
        inspect_splat_geometry,
        observed_bounds,
    )

    object_geometry = None
    object_gaussians = prepare(data)
    if support is not None:
        object_geometry = inspect_splat_geometry(
            data, float(support.scene_scale), object_id=str(support.object_id)
        )
        if not object_geometry["gaussians"]:
            raise ObjectSupportError(f"{support.object_id}: no finite supported Gaussians remain")
        # Raw spatial heuristics never delete source rows: a legitimate thin or
        # disconnected part may be an outlier to a coordinate statistic.  The
        # mask/depth/multiview gates below decide which rendered samples have
        # evidence and record that decision in the manifest.

    scene_gaussians = None
    if support is not None and scene_ply_path is not None:
        scene_path = Path(scene_ply_path)
        if not scene_path.is_file() or scene_path.is_symlink():
            raise ObjectSupportError(
                f"{support.object_id}: full scene PLY required for occlusion checks, missing {scene_path}"
            )
        scene_data = read_splat_ply(scene_path)
        scene_gaussians = prepare(scene_data)

    chosen = indices if indices is not None else list(range(len(views)))
    if support is not None:
        missing = [index for index in chosen if index not in support.view_indices]
        if missing:
            raise ObjectSupportError(
                f"{support.object_id}: depth fusion requested unsupported ViewSet indices {missing[:5]}"
            )
        if len(chosen) < support.min_consistent_views:
            raise ObjectSupportError(
                f"{support.object_id}: at least {support.min_consistent_views} supported views are required"
            )
    clouds: list[np.ndarray] = []
    colours: list[np.ndarray] = []

    def render(gaussians, batch):
        return rasterization(
            means=gaussians["means"], quats=gaussians["quats"],
            scales=gaussians["scales"], opacities=gaussians["opacities"],
            colors=gaussians["sh"], viewmats=batch.world_to_camera, Ks=batch.intrinsics,
            width=batch.width, height=batch.height, sh_degree=gaussians["degree"],
            packed=True, rasterize_mode="antialiased", render_mode="RGB+ED",
            near_plane=float(views.scene_scale) * 1e-5,
            far_plane=float(views.scene_scale) * 1e4,
        )

    with torch.no_grad():
        for count, index in enumerate(chosen):
            batch = views.full(index, max_long_edge=max_long_edge)
            rendered, alpha, _ = render(object_gaussians, batch)
            image = rendered[0, ..., :3].clamp(0, 1).cpu().numpy()
            depth = rendered[0, ..., 3].cpu().numpy()
            coverage = alpha[0, ..., 0].cpu().numpy()

            # Only trust pixels where enough opacity accumulated; elsewhere the
            # "depth" is an average over near-transparent space.
            depth = np.where(coverage > 0.5, depth, np.nan)

            if support is not None:
                # Masks are loaded and transformed against the exact ViewSet
                # image.  A silhouette alone is insufficient: opacity and
                # finite positive depth must also support every fused pixel.
                mask = support.mask_for(index, width=batch.width, height=batch.height)
                depth = np.where(mask, depth, np.nan)
                support.depth_maps[index] = depth.copy()
                support.view_shapes[index] = (batch.height, batch.width)

            if scene_gaussians is not None:
                scene_rendered, scene_alpha, _ = render(scene_gaussians, batch)
                scene_depth = scene_rendered[0, ..., 3].cpu().numpy()
                scene_coverage = scene_alpha[0, ..., 0].cpu().numpy()
                scene_depth = np.where(scene_coverage > 0.5, scene_depth, np.nan)
                # ED is kept in camera-Z convention by the gsplat backend used
                # by this project.  The convention is recorded and compared
                # in the same space; callers cannot silently mix ray distance
                # with camera-Z.  A small relative tolerance covers splat
                # thickness while rejecting an object hidden behind the scene.
                occluded = np.isfinite(scene_depth) & np.isfinite(depth)
                tolerance = np.maximum(np.abs(scene_depth) * 0.05, float(views.scene_scale) * 0.02)
                depth = np.where(occluded & (depth > scene_depth + tolerance), np.nan, depth)
                if support is not None:
                    support.scene_depth_maps[index] = scene_depth

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
    diagnostics = {
        "views_requested": len(chosen),
        "views_with_depth": len(clouds),
        "raw_points": int(len(points)),
        "depth_convention": "camera_z",
    }
    if support is not None:
        # Reproject each back-projected point into the other selected views and
        # retain points with true multi-view mask/depth agreement.  This is
        # what prevents an in-mask but unobserved sheet from becoming a mesh.
        from .object_support import filter_multiview_points
        points, point_colours, consistency = filter_multiview_points(
            points, point_colours, support, views,
        )
        diagnostics.update(consistency)
        if len(points) < 32:
            raise ObjectSupportError(
                f"{support.object_id}: insufficient multi-view supported points ({len(points)})"
            )
        support.observed_bounds = observed_bounds(points, support.scene_scale)
        support.diagnostics.update(diagnostics)
    logger.info("fused %d views into %s points", len(chosen), f"{len(points):,}")
    return (points, point_colours, diagnostics) if return_diagnostics else (points, point_colours)


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

    ``keep_fraction`` is an experimental MeshLab *volumetric obscurance*
    scalar threshold.  It is not a measured camera/mask support value and
    must not be used as an object acceptance gate.  The evidence-backed object
    path passes zero and performs its own mask/depth/visibility checks after
    meshing.  Poisson can still close unseen regions, so every output remains
    explicitly experimental until that post-check succeeds.
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

    logger.info("estimating point-cloud normals (MeshLab orientation; outward orientation is unverified)")
    mesh_set.compute_normal_for_point_clouds(k=16, smoothiter=2)

    logger.info("screened Poisson reconstruction (depth=%d)", depth)
    mesh_set.generate_surface_reconstruction_screened_poisson(depth=depth, preclean=True)

    if keep_fraction > 0:
        logger.warning(
            "applying experimental MeshLab volumetric-obscurance trim q < %.3f; "
            "this is not measured object support",
            keep_fraction,
        )
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
    trim_fraction: float = 0.12,
    max_long_edge: int = 1200,
    support=None,
    scene_ply_path: Path | None = None,
) -> Path:
    """Splat to mesh, end to end."""
    if max_views < 1 or len(views) < 1:
        raise ValueError("At least one camera view is required")
    if support is not None:
        # Object fusion may only use frames whose exact mask/instance lineage
        # was bound to this ViewSet.  Sampling the whole scene here would mix
        # unsupported cameras into an object reconstruction.
        indices = list(support.view_indices)
        if len(indices) > max_views:
            indices = indices[:max_views]
    else:
        indices = np.linspace(0, len(views)-1, min(max_views, len(views)), dtype=int).tolist()
    points, colours = splat_to_pointcloud(
        ply_path, views, indices=indices, max_long_edge=max_long_edge,
        support=support, scene_ply_path=scene_ply_path,
    )
    result = poisson_mesh(points, colours, out_path, depth=depth, keep_fraction=trim_fraction)
    if support is not None:
        from .object_support import validate_mesh_support
        from plyfile import PlyData
        data = PlyData.read(str(result), mmap=False)
        vertex = data["vertex"]
        vertices = np.column_stack([vertex[key] for key in ("x", "y", "z")])
        faces = np.asarray(data["face"]["vertex_indices"], dtype=object)
        support.diagnostics["mesh"] = validate_mesh_support(vertices, faces, support, views)
    return result
