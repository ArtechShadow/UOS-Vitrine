"""Read and write the standard 3D Gaussian Splatting PLY format.

This is the interchange format every 3DGS viewer understands, and the one the
preservation package deposits as its master. Property layout, per the original
Inria implementation:

    x, y, z                     position
    nx, ny, nz                  normals — always zero, kept for compatibility
    f_dc_0..2                   SH band 0 (base colour)
    f_rest_0..N                 SH bands 1..d, **channel-major**
    opacity                     logit-space
    scale_0..2                  log-space
    rot_0..3                    quaternion, wxyz

Two details cause most interoperability bugs:

**Spherical harmonics are channel-major on disk.** In memory the natural layout
is ``[N, coeffs, 3]``; on disk it is all red coefficients, then all green, then
all blue. Writing it interleaved produces a file that loads without complaint
and renders with badly wrong colour.

**Opacity and scale are stored pre-activation** — logit and log respectively.
Writing activated values yields a model that looks washed out and oversized.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import uuid

import numpy as np
from plyfile import PlyData, PlyElement

logger = logging.getLogger(__name__)

#: Zeroth-order spherical harmonic coefficient, 1 / (2 * sqrt(pi)).
SH_C0 = 0.28209479177387814


def sh_coefficient_count(degree: int) -> int:
    """Number of *rest* SH coefficients per channel for a given degree."""
    return (degree + 1) ** 2 - 1


def rgb_to_sh_dc(rgb: np.ndarray) -> np.ndarray:
    """Convert linear RGB in [0,1] to band-0 SH coefficients."""
    return (rgb - 0.5) / SH_C0


def sh_dc_to_rgb(dc: np.ndarray) -> np.ndarray:
    return dc * SH_C0 + 0.5


def write_splat_ply(
    path: Path,
    *,
    means: np.ndarray,        # [N, 3]
    scales: np.ndarray,       # [N, 3] log-space
    quats: np.ndarray,        # [N, 4] wxyz
    opacities: np.ndarray,    # [N] logit-space
    sh0: np.ndarray,          # [N, 1, 3]
    shN: np.ndarray,          # [N, K, 3]
    sh_degree: int,
    max_scale: float | None = None,
) -> Path:
    """Write Gaussians to a standard 3DGS PLY.

    ``max_scale`` is a **linear world-unit** ceiling on Gaussian radius. Pass a
    multiple of the scene scale, never a constant: a COLMAP reconstruction has
    arbitrary units, so a hard-coded limit that tames one scene will shred the
    walls of a differently-sized one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = len(means)
    n_rest = sh_coefficient_count(sh_degree)

    means = np.ascontiguousarray(means, dtype=np.float32)
    scales = np.ascontiguousarray(scales, dtype=np.float32)
    quats = np.ascontiguousarray(quats, dtype=np.float32)
    opacities = np.ascontiguousarray(opacities, dtype=np.float32).reshape(count)

    if max_scale is not None and max_scale > 0:
        ceiling = float(np.log(max_scale))
        clipped = int((scales > ceiling).any(axis=1).sum())
        if clipped:
            logger.info(
                "clamped %d/%d Gaussians to a %.4f world-unit radius (%.1f%%)",
                clipped, count, max_scale, clipped / max(count, 1) * 100,
            )

    properties: list[tuple[str, str]] = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
    ]
    properties += [(f"f_dc_{i}", "f4") for i in range(3)]
    properties += [(f"f_rest_{i}", "f4") for i in range(n_rest * 3)]
    properties += [("opacity", "f4")]
    properties += [(f"scale_{i}", "f4") for i in range(3)]
    properties += [(f"rot_{i}", "f4") for i in range(4)]

    # Bound the additional host allocation during export. A full SH3 master
    # formerly needed another complete structured array beside its parameters
    # and image cache. Publish atomically so interruption cannot replace a good
    # master with a partial PLY.
    temporary = path.with_name(path.name + '.writing-' + uuid.uuid4().hex)
    header = ['ply', 'format binary_little_endian 1.0', f'element vertex {count}']
    header += [f'property float {name}' for name, _ in properties]
    header += ['end_header', '']
    try:
        with temporary.open('wb') as handle:
            handle.write('\n'.join(header).encode('ascii'))
            for start in range(0, count, 65536):
                end = min(count, start + 65536)
                array = np.zeros(end-start, dtype=[(name, '<f4') for name, _ in properties])
                for i, axis in enumerate(('x', 'y', 'z')):
                    array[axis] = means[start:end, i]
                    array[f'f_dc_{i}'] = sh0[start:end, 0, i]
                for channel in range(3):
                    for band in range(min(n_rest, shN.shape[1])):
                        array[f'f_rest_{channel*n_rest+band}'] = shN[start:end, band, channel]
                array['opacity'] = opacities[start:end]
                chunk_scales = scales[start:end]
                if max_scale is not None and max_scale > 0:
                    chunk_scales = np.minimum(chunk_scales, ceiling)
                chunk_quats = quats[start:end]
                chunk_quats = chunk_quats / np.clip(np.linalg.norm(chunk_quats, axis=1, keepdims=True), 1e-8, None)
                for i in range(3):
                    array[f'scale_{i}'] = chunk_scales[:, i]
                for i in range(4):
                    array[f'rot_{i}'] = chunk_quats[:, i]
                array.tofile(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    size_mb = path.stat().st_size / 1e6
    logger.info("wrote %s — %d Gaussians, SH degree %d, %.1f MB", path.name, count, sh_degree, size_mb)
    return path


def read_splat_ply(path: Path) -> dict[str, np.ndarray | int]:
    """Read a 3DGS PLY back into arrays. Used for comparison and validation."""
    data = PlyData.read(str(path))
    vertex = data["vertex"]
    names = set(vertex.data.dtype.names or ())

    count = len(vertex["x"])
    means = np.stack([vertex["x"], vertex["y"], vertex["z"]], axis=1)
    scales = np.stack([vertex[f"scale_{i}"] for i in range(3)], axis=1)
    quats = np.stack([vertex[f"rot_{i}"] for i in range(4)], axis=1)
    opacities = np.asarray(vertex["opacity"])
    sh0 = np.stack([vertex[f"f_dc_{i}"] for i in range(3)], axis=1).reshape(count, 1, 3)

    n_rest_total = sum(1 for n in names if n.startswith("f_rest_"))
    per_channel = n_rest_total // 3
    degree = int(round(np.sqrt(per_channel + 1))) - 1 if per_channel else 0

    if per_channel:
        shN = np.zeros((count, per_channel, 3), dtype=np.float32)
        for channel in range(3):
            for band in range(per_channel):
                shN[:, band, channel] = vertex[f"f_rest_{channel * per_channel + band}"]
    else:
        shN = np.zeros((count, 0, 3), dtype=np.float32)

    return {
        "means": means, "scales": scales, "quats": quats,
        "opacities": opacities, "sh0": sh0, "shN": shN,
        "sh_degree": degree, "count": count,
    }
