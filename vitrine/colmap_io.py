"""Read COLMAP sparse models.

We deliberately parse the **text** form (``cameras.txt`` / ``images.txt`` /
``points3D.txt``) rather than the binary one. COLMAP writes binary by default,
so the pipeline runs ``model_converter`` once — and the text model it produces
is the same artefact the preservation package needs to deposit. Producing the
archival copy as a side effect of normal processing, rather than as a separate
export step someone has to remember, is the point.

**Multi-camera is a first-class case here.** A capture that mixes 25 MP stills
with 720p video frames yields two camera entries with different intrinsics and
different image dimensions. Code that grabs ``cameras[0]`` and applies it to
everything will silently produce a warped reconstruction rather than an error,
so every image carries its own camera reference throughout this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# COLMAP camera models, by the parameter list each one stores.
# Reference: colmap/src/colmap/sensor/models.h
_CAMERA_MODEL_PARAMS: dict[str, tuple[str, ...]] = {
    "SIMPLE_PINHOLE": ("f", "cx", "cy"),
    "PINHOLE": ("fx", "fy", "cx", "cy"),
    "SIMPLE_RADIAL": ("f", "cx", "cy", "k1"),
    "RADIAL": ("f", "cx", "cy", "k1", "k2"),
    "OPENCV": ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2"),
    "OPENCV_FISHEYE": ("fx", "fy", "cx", "cy", "k1", "k2", "k3", "k4"),
    "FULL_OPENCV": ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6"),
    "FOV": ("fx", "fy", "cx", "cy", "omega"),
    "SIMPLE_RADIAL_FISHEYE": ("f", "cx", "cy", "k1"),
    "RADIAL_FISHEYE": ("f", "cx", "cy", "k1", "k2"),
    "THIN_PRISM_FISHEYE": ("fx", "fy", "cx", "cy", "k1", "k2", "p1", "p2", "k3", "k4", "sx1", "sy1"),
}


@dataclass(frozen=True)
class Camera:
    """One physical camera / intrinsic model."""

    id: int
    model: str
    width: int
    height: int
    params: dict[str, float]

    @property
    def fx(self) -> float:
        return self.params.get("fx", self.params.get("f", 0.0))

    @property
    def fy(self) -> float:
        return self.params.get("fy", self.params.get("f", 0.0))

    @property
    def cx(self) -> float:
        return self.params["cx"]

    @property
    def cy(self) -> float:
        return self.params["cy"]

    def intrinsic_matrix(self) -> np.ndarray:
        """3x3 K at the camera's native resolution."""
        return np.array(
            [[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]],
            dtype=np.float32,
        )

    def scaled_intrinsics(self, width: int, height: int) -> np.ndarray:
        """K rescaled to a different image size.

        Training happens on resized copies, so intrinsics must move with them.
        x and y scale independently — assuming a single factor silently skews
        the projection whenever a resize is not perfectly proportional.
        """
        sx = width / self.width
        sy = height / self.height
        k = self.intrinsic_matrix()
        k[0, :] *= sx
        k[1, :] *= sy
        return k

    @property
    def has_distortion(self) -> bool:
        return any(abs(self.params.get(p, 0.0)) > 1e-8 for p in ("k1", "k2", "k3", "p1", "p2"))


@dataclass(frozen=True)
class Image:
    """One registered view: a pose plus the camera that took it."""

    id: int
    name: str
    camera_id: int
    #: World-to-camera rotation as a wxyz quaternion.
    qvec: np.ndarray
    #: World-to-camera translation.
    tvec: np.ndarray

    def rotation_matrix(self) -> np.ndarray:
        return quaternion_to_rotation(self.qvec)

    def world_to_camera(self) -> np.ndarray:
        """4x4 world-to-camera matrix (gsplat's ``viewmats`` convention)."""
        m = np.eye(4, dtype=np.float32)
        m[:3, :3] = self.rotation_matrix()
        m[:3, 3] = self.tvec
        return m

    def camera_centre(self) -> np.ndarray:
        """Camera position in world coordinates: ``-R^T t``."""
        return -self.rotation_matrix().T @ self.tvec


@dataclass
class Model:
    """A complete sparse reconstruction."""

    cameras: dict[int, Camera]
    images: list[Image]
    points_xyz: np.ndarray  # [M, 3] float32
    points_rgb: np.ndarray  # [M, 3] float32 in [0, 1]

    def camera_for(self, image: Image) -> Camera:
        return self.cameras[image.camera_id]

    @property
    def is_multi_camera(self) -> bool:
        return len(self.cameras) > 1

    def summary(self) -> str:
        parts = [
            f"{len(self.images)} images",
            f"{len(self.cameras)} camera model(s)",
            f"{len(self.points_xyz):,} points",
        ]
        for cam in self.cameras.values():
            n = sum(1 for im in self.images if im.camera_id == cam.id)
            parts.append(f"cam{cam.id}={cam.model} {cam.width}x{cam.height} ({n} images)")
        return " · ".join(parts)


def quaternion_to_rotation(q: np.ndarray) -> np.ndarray:
    """wxyz quaternion to a 3x3 rotation matrix."""
    w, x, y, z = np.asarray(q, dtype=np.float64) / np.linalg.norm(q)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float32,
    )


def _iter_data_lines(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                yield line


def read_cameras(path: Path) -> dict[int, Camera]:
    """Parse ``cameras.txt``: ``ID MODEL WIDTH HEIGHT PARAMS...``"""
    cameras: dict[int, Camera] = {}
    for line in _iter_data_lines(path):
        parts = line.split()
        cam_id = int(parts[0])
        model = parts[1]
        width, height = int(parts[2]), int(parts[3])
        values = [float(v) for v in parts[4:]]

        names = _CAMERA_MODEL_PARAMS.get(model)
        if names is None:
            raise ValueError(
                f"unsupported COLMAP camera model {model!r} in {path}. "
                f"Known models: {sorted(_CAMERA_MODEL_PARAMS)}"
            )
        if len(values) != len(names):
            raise ValueError(
                f"camera {cam_id} ({model}) expects {len(names)} parameters, got {len(values)}"
            )

        cameras[cam_id] = Camera(cam_id, model, width, height, dict(zip(names, values)))
    return cameras


def read_images(path: Path) -> list[Image]:
    """Parse ``images.txt``.

    Each image occupies two lines — pose then 2D observations. We keep the
    pose and skip the observation line.
    """
    images: list[Image] = []
    lines = list(_iter_data_lines(path))

    # Observation lines are the odd-indexed ones. They can legitimately be
    # empty, in which case COLMAP still writes a blank line — which
    # _iter_data_lines has already dropped. So rather than assuming strict
    # pairing, detect pose lines by their shape: 10 fields, first is an int.
    index = 0
    while index < len(lines):
        parts = lines[index].split()
        index += 1
        if len(parts) < 10:
            continue  # an observation line; skip
        try:
            image_id = int(parts[0])
        except ValueError:
            continue
        qvec = np.array([float(v) for v in parts[1:5]], dtype=np.float64)
        tvec = np.array([float(v) for v in parts[5:8]], dtype=np.float32)
        camera_id = int(parts[8])
        name = " ".join(parts[9:])  # filenames may contain spaces
        images.append(Image(image_id, name, camera_id, qvec, tvec))
        index += 1  # consume the observation line that follows

    return images


def read_points(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Parse ``points3D.txt`` into ``(xyz [M,3], rgb [M,3] in [0,1])``."""
    xyz: list[tuple[float, float, float]] = []
    rgb: list[tuple[float, float, float]] = []
    for line in _iter_data_lines(path):
        parts = line.split()
        if len(parts) < 7:
            continue
        xyz.append((float(parts[1]), float(parts[2]), float(parts[3])))
        rgb.append((float(parts[4]), float(parts[5]), float(parts[6])))

    if not xyz:
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32)
    return (
        np.array(xyz, dtype=np.float32),
        np.array(rgb, dtype=np.float32) / 255.0,
    )


def read_model(sparse_dir: Path) -> Model:
    """Read a full text model from a directory holding the three ``.txt`` files."""
    sparse_dir = Path(sparse_dir)
    required = ["cameras.txt", "images.txt", "points3D.txt"]
    missing = [n for n in required if not (sparse_dir / n).is_file()]
    if missing:
        raise FileNotFoundError(
            f"{sparse_dir} is not a COLMAP text model — missing {missing}. "
            "Run `colmap model_converter --output_type TXT` on the binary model first."
        )

    cameras = read_cameras(sparse_dir / "cameras.txt")
    images = read_images(sparse_dir / "images.txt")
    xyz, rgb = read_points(sparse_dir / "points3D.txt")

    unknown = {im.camera_id for im in images} - set(cameras)
    if unknown:
        raise ValueError(f"images.txt references camera ids not in cameras.txt: {sorted(unknown)}")

    model = Model(cameras, images, xyz, rgb)
    logger.info("COLMAP model: %s", model.summary())
    return model


def scene_scale(model: Model) -> float:
    """Half the diagonal of the camera-centre bounding box.

    Used to set densification thresholds and the export scale clamp in units
    the scene actually has, rather than hard-coded world units — a COLMAP
    reconstruction has arbitrary scale, so any absolute threshold is a bug
    waiting for a differently-sized room.
    """
    if not model.images:
        return 1.0
    centres = np.stack([im.camera_centre() for im in model.images])
    extent = centres.max(axis=0) - centres.min(axis=0)
    return max(float(np.linalg.norm(extent)) / 2.0, 1e-3)
