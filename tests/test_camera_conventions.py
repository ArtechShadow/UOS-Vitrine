"""Deterministic calibration contracts, not capture-quality evidence."""
import numpy as np
import pytest
import torch
from vitrine.colmap_io import Camera
from vitrine.undistort import _distort, undistort
from vitrine.dataset import _locate_image


def test_full_opencv_denominator_is_applied():
    x, y = torch.tensor([1.0]), torch.tensor([0.0])
    dx, dy = _distort(x, y, {"k4": 1.0})
    assert dx.item() == pytest.approx(0.5)
    assert dy.item() == 0
    assert Camera(1, 'FULL_OPENCV', 32, 32, {'k4': 1}).has_distortion


def test_singular_rational_distortion_is_rejected():
    with pytest.raises(ValueError, match='denominator'):
        _distort(torch.ones(1), torch.zeros(1), {'k4': -1})


def test_mask_and_image_share_rectification_crop_and_intrinsics():
    camera = Camera(3, 'OPENCV', 64, 48, dict(fx=40, fy=40, cx=32, cy=24, k1=.12))
    k = torch.from_numpy(camera.intrinsic_matrix())
    mask = torch.zeros(48, 64, 1)
    mask[12:36, 16:48] = 1
    image, ik = undistort(mask.repeat(1, 1, 3), k, camera)
    rectified, mk = undistort(mask, k, camera, mode='nearest')
    assert image.shape[:2] == rectified.shape[:2]
    assert torch.equal(ik, mk)
    assert set(rectified.unique().tolist()) <= {0, 1}
    assert np.allclose(k.numpy(), camera.intrinsic_matrix())


def test_unsupported_camera_is_not_silently_pinhole():
    camera = Camera(3, 'OPENCV_FISHEYE', 32, 32, dict(fx=20, fy=20, cx=16, cy=16))
    with pytest.raises(ValueError, match='supported rectification'):
        undistort(torch.zeros(32, 32, 3), torch.eye(3), camera)


def test_duplicate_basename_is_not_an_image_association(tmp_path):
    for folder in ['camera-a', 'camera-b']:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / 'same.jpg').write_bytes(b'fixture')
    with pytest.raises(ValueError, match='Ambiguous'):
        _locate_image(tmp_path, 'missing/same.jpg')
    assert _locate_image(tmp_path, 'camera-b/same.jpg') == tmp_path / 'camera-b/same.jpg'


def test_view_retains_precrop_calibration_and_noncontiguous_image_id(tmp_path):
    from PIL import Image as PILImage
    from vitrine.colmap_io import Image, Model
    from vitrine.dataset import ViewSet
    camera = Camera(7, 'OPENCV', 64, 48, dict(fx=40, fy=40, cx=32, cy=24, k1=.12))
    image = Image(903, 'frame.jpg', 7, np.array([1., 0, 0, 0]), np.zeros(3))
    model = Model({7: camera}, [image], np.array([[0., 0., 1.], [1., 1., 2.]]), np.ones((2, 3)))
    PILImage.new('RGB', (64, 48), 'white').save(tmp_path/'frame.jpg')
    view = ViewSet(model, tmp_path, long_edge=64, device='cpu', ram_guard=False, holdout_every=0).views[0]
    assert view.image_id == 903
    assert view.rectification_input_size == (64, 48)
    assert torch.equal(view.rectification_input_intrinsics, torch.from_numpy(camera.intrinsic_matrix()))
    assert view.width <= 64 and view.height <= 48
