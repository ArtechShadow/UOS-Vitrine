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
import hashlib
import os
from pathlib import Path
import uuid

import numpy as np
from plyfile import PlyData, PlyElement

logger = logging.getLogger(__name__)

#: Zeroth-order spherical harmonic coefficient, 1 / (2 * sqrt(pi)).
SH_C0 = 0.28209479177387814

# The binary layout is deliberately kept local to this module.  The viewer's
# ``.splat`` derivative has a different compact format; the PLY is the
# preservation master and must remain independently inspectable.
_PLY_HEADER_LIMIT = 16 * 1024 * 1024
_PLY_VERIFY_CHUNK = 65_536


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file without loading it into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_binary_header(path: Path) -> tuple[int, list[str], int]:
    """Read and validate the binary vertex header used by Vitrine masters.

    Returning the byte offset and property names lets save verification scan a
    full-SH file in bounded chunks.  ``PlyData.read`` is still used by the
    public round-trip reader, but a final save should not need another complete
    structured-array allocation just to discover a truncated file.
    """
    header = bytearray()
    with Path(path).open("rb") as handle:
        while len(header) <= _PLY_HEADER_LIMIT:
            line = handle.readline(_PLY_HEADER_LIMIT - len(header) + 1)
            if not line:
                raise ValueError("PLY header is incomplete")
            if len(header) + len(line) > _PLY_HEADER_LIMIT:
                raise ValueError("PLY header exceeds the safety limit")
            header.extend(line)
            if line.rstrip(b"\r\n") == b"end_header":
                break
        else:
            raise ValueError("PLY header exceeds the safety limit")

        try:
            lines = header.decode("ascii").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("PLY header is not ASCII") from exc
        if not lines or lines[0].strip() != "ply":
            raise ValueError("Not a PLY file")
        if "format binary_little_endian 1.0" not in lines:
            raise ValueError("PLY is not binary_little_endian 1.0")
        if not lines or lines[-1].strip() != "end_header":
            raise ValueError("PLY header has no end_header")

        count = None
        properties: list[str] = []
        active_element = None
        for line in lines[1:-1]:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "element":
                if len(parts) != 3:
                    raise ValueError("Invalid PLY element declaration")
                active_element = parts[1]
                if active_element == "vertex":
                    try:
                        count = int(parts[2])
                    except ValueError as exc:
                        raise ValueError("Invalid PLY vertex count") from exc
                    if count < 0:
                        raise ValueError("PLY vertex count is negative")
            elif parts[0] == "property" and active_element == "vertex":
                if len(parts) != 3 or parts[1] != "float":
                    raise ValueError("PLY vertex properties must be float scalars")
                properties.append(parts[2])
        if count is None:
            raise ValueError("PLY has no vertex element")
        if not properties:
            raise ValueError("PLY has no vertex properties")
        if len(set(properties)) != len(properties):
            raise ValueError("PLY has duplicate vertex properties")
        return count, properties, handle.tell()


def verify_splat_ply(
    path: Path,
    *,
    expected_count: int | None = None,
    expected_sh_degree: int | None = None,
    expected_sha256: str | None = None,
    require_nonempty: bool = False,
) -> dict[str, int | str | bool]:
    """Validate a Vitrine Gaussian PLY and return a durable file receipt.

    The check is intentionally stricter than merely testing file existence:
    it validates the header, exact binary length, required 3DGS properties,
    finite values and quaternion norms in bounded chunks.  This is suitable for
    the post-write gate before a trained model is exposed as ``scene.ply``.

    ``expected_sha256`` is useful when reopening an already published artifact;
    it is compared after the same structural scan.  No caller should treat a
    structurally valid PLY as visual or semantic acceptance of a reconstruction.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"PLY does not exist: {path}")
    count, properties, data_offset = _read_binary_header(path)
    if expected_count is not None and count != expected_count:
        raise ValueError(f"PLY vertex count {count} does not match expected {expected_count}")
    if require_nonempty and count == 0:
        raise ValueError("PLY contains no Gaussian records")

    required = {
        "x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
        "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3",
    }
    missing = sorted(required - set(properties))
    if missing:
        raise ValueError("PLY is missing required 3DGS properties: " + ", ".join(missing))
    rest = sorted(
        (name for name in properties if name.startswith("f_rest_")),
        key=lambda name: int(name.removeprefix("f_rest_")) if name.removeprefix("f_rest_").isdigit() else -1,
    )
    expected_rest = [f"f_rest_{index}" for index in range(len(rest))]
    if rest != expected_rest:
        raise ValueError("PLY SH coefficients are not contiguous and channel-major")
    if len(rest) % 3:
        raise ValueError("PLY has an incomplete channel-major SH coefficient set")
    per_channel = len(rest) // 3
    sh_degree = int(round(np.sqrt(per_channel + 1))) - 1 if per_channel else 0
    if sh_coefficient_count(sh_degree) != per_channel:
        raise ValueError(f"PLY has an invalid SH coefficient count: {len(rest)}")
    if expected_sh_degree is not None and sh_degree != expected_sh_degree:
        raise ValueError(f"PLY SH degree {sh_degree} does not match expected {expected_sh_degree}")

    stride = len(properties) * np.dtype("<f4").itemsize
    expected_bytes = data_offset + count * stride
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise ValueError(
            f"PLY binary payload is incomplete or has trailing bytes ({actual_bytes} != {expected_bytes})"
        )
    dtype = np.dtype([(name, "<f4") for name in properties])
    finite_fields = [name for name in properties]
    quaternion_fields = [f"rot_{i}" for i in range(4)]
    with path.open("rb") as handle:
        handle.seek(data_offset)
        remaining = count
        while remaining:
            take = min(remaining, _PLY_VERIFY_CHUNK)
            values = np.fromfile(handle, dtype=dtype, count=take)
            if len(values) != take:
                raise ValueError("PLY payload ended before the declared vertex count")
            if not np.isfinite(np.column_stack([values[name] for name in finite_fields])).all():
                raise ValueError("PLY contains non-finite Gaussian values")
            norms = np.sqrt(sum(values[name] ** 2 for name in quaternion_fields))
            if np.any(norms <= 1e-8):
                raise ValueError("PLY contains a zero-length quaternion")
            remaining -= take

    checksum = sha256_file(path)
    if expected_sha256 is not None and checksum != expected_sha256:
        raise ValueError(f"PLY SHA-256 {checksum} does not match expected {expected_sha256}")
    return {
        "path": str(path),
        "bytes": actual_bytes,
        "count": count,
        "sh_degree": sh_degree,
        "sha256": checksum,
        "finite": True,
        "format": "binary_little_endian_1.0",
    }


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
    means = np.ascontiguousarray(means, dtype=np.float32)
    scales = np.ascontiguousarray(scales, dtype=np.float32)
    quats = np.ascontiguousarray(quats, dtype=np.float32)
    opacities = np.ascontiguousarray(opacities, dtype=np.float32).reshape(-1)
    sh0 = np.ascontiguousarray(sh0, dtype=np.float32)
    shN = np.ascontiguousarray(shN, dtype=np.float32)
    if not isinstance(sh_degree, (int, np.integer)) or sh_degree < 0:
        raise ValueError("SH degree must be a non-negative integer")
    n_rest = sh_coefficient_count(int(sh_degree))
    if means.shape != (count, 3):
        raise ValueError(f"means must have shape ({count}, 3), got {means.shape}")
    if scales.shape != (count, 3):
        raise ValueError(f"scales must have shape ({count}, 3), got {scales.shape}")
    if quats.shape != (count, 4):
        raise ValueError(f"quats must have shape ({count}, 4), got {quats.shape}")
    if opacities.shape != (count,):
        raise ValueError(f"opacities must have shape ({count},), got {opacities.shape}")
    if sh0.shape != (count, 1, 3):
        raise ValueError(f"sh0 must have shape ({count}, 1, 3), got {sh0.shape}")
    if shN.ndim != 3 or shN.shape[0] != count or shN.shape[2] != 3 or shN.shape[1] < n_rest:
        raise ValueError(
            f"shN must have shape ({count}, >= {n_rest}, 3), got {shN.shape}"
        )
    if not all(np.isfinite(array).all() for array in (means, scales, quats, opacities, sh0, shN)):
        raise ValueError("Cannot write a PLY containing non-finite Gaussian values")
    quat_norms = np.linalg.norm(quats, axis=1)
    if np.any(quat_norms <= 1e-8):
        raise ValueError("Cannot write a PLY containing a zero-length quaternion")
    if max_scale is not None and (not np.isfinite(max_scale) or max_scale <= 0):
        raise ValueError("max_scale must be a positive finite value when provided")

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
        # Validate the completed temporary before replacing an existing master.
        # A writer error or an interrupted process therefore leaves the previous
        # ``path`` untouched and leaves no partial file at the public name.
        verify_splat_ply(
            temporary,
            expected_count=count,
            expected_sh_degree=int(sh_degree),
            require_nonempty=False,
        )
        os.replace(temporary, path)
        # POSIX needs a directory sync for the rename itself to survive a power
        # loss.  Windows does not permit opening directories this way, so the
        # best available guarantee there is the flushed file plus atomic replace.
        if os.name != "nt":
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                logger.debug("Could not fsync PLY parent directory", exc_info=True)
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
