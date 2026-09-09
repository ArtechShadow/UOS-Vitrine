"""Deterministic contracts for evidence-backed object reconstruction.

These tests exercise identity/calibration/support decisions only.  They do not
claim that synthetic points or masks are a real-capture quality result.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image

from vitrine.colmap_io import Camera, Image as ColmapImage, Model
from vitrine.dataset import Batch, View
from vitrine.object_support import (
    MissingSupportEvidence,
    ObjectSupport,
    ObjectSupportError,
    SupportObservation,
    bind_object_support,
    filter_multiview_points,
    inspect_splat_geometry,
    load_mask_for_view,
    parse_support_observations,
    select_supporting_observations,
    validate_mesh_support,
)


def _model(*, ids=(10, 20, 30), camera_ids=(1, 1, 1), names=None):
    camera = Camera(1, "PINHOLE", 8, 8, {"fx": 4.0, "fy": 4.0, "cx": 4.0, "cy": 4.0})
    names = names or tuple(f"cam/{value}.png" for value in ids)
    images = []
    for position, (image_id, camera_id, name) in enumerate(zip(ids, camera_ids, names)):
        images.append(ColmapImage(
            image_id, name, camera_id, np.array([1.0, 0, 0, 0]),
            np.array([float(position) * 2.0, 0.0, 0.0]),
        ))
    return Model({1: camera}, images, np.zeros((1, 3), np.float32), np.zeros((1, 3), np.float32))


def _evidence(model, root: Path, *, same_image=False):
    entries = []
    for index, image in enumerate(model.images):
        mask = root / f"mask-{index}.png"
        Image.fromarray(np.full((8, 8), 255, dtype=np.uint8)).save(mask)
        entries.append({
            "image_id": image.id,
            "image_name": image.name,
            "camera_id": image.camera_id,
            "instance_id": f"inst-{index}",
            "mask_path": mask.name,
            "mask_sha256": hashlib.sha256(mask.read_bytes()).hexdigest(),
            "image_width": 8,
            "image_height": 8,
            "mask_width": 8,
            "mask_height": 8,
            "coordinate_space": "rectified",
        })
    if same_image:
        entries[1]["image_id"] = entries[0]["image_id"]
        entries[1]["image_name"] = entries[0]["image_name"]
        entries[1]["camera_id"] = entries[0]["camera_id"]
    return entries


def test_same_frame_conflicting_masks_are_rejected():
    record = {"object_id": "candidate_01", "provenance": {"evidence": [
        {"image_id": 10, "image_name": "a.png", "camera_id": 1, "instance_id": "a", "mask_path": "a.png", "mask_sha256": "a" * 64, "coordinate_space": "view"},
        {"image_id": 10, "image_name": "a.png", "camera_id": 1, "instance_id": "b", "mask_path": "b.png", "mask_sha256": "b" * 64, "coordinate_space": "view"},
    ]}}
    with pytest.raises(MissingSupportEvidence, match="conflicts"):
        parse_support_observations(record)


def test_mask_without_instance_identity_is_rejected():
    record = {"object_id": "candidate_01", "provenance": {"evidence": [
        {"image_id": 10, "image_name": "a.png", "camera_id": 1, "mask_path": "a.png", "mask_sha256": "a" * 64, "coordinate_space": "view"},
    ]}}
    with pytest.raises(MissingSupportEvidence, match="instance_id or detection_id"):
        parse_support_observations(record)


def test_frame_number_cannot_stand_in_for_colmap_image_id():
    record = {"object_id": "candidate_01", "provenance": {"evidence": [
        {"frame_id": 10, "image_name": "a.png", "camera_id": 1,
         "instance_id": "a", "mask_path": "a.png", "mask_sha256": "a" * 64,
         "coordinate_space": "view"},
    ]}}
    with pytest.raises(MissingSupportEvidence, match="COLMAP image identity"):
        parse_support_observations(record)


def test_mask_checksum_is_required():
    record = {"object_id": "candidate_01", "provenance": {"evidence": [
        {"image_id": 10, "image_name": "a.png", "camera_id": 1,
         "instance_id": "a", "mask_path": "a.png", "coordinate_space": "view"},
    ]}}
    with pytest.raises(MissingSupportEvidence, match="mask_sha256"):
        parse_support_observations(record)


def test_incomplete_import_status_cannot_use_partial_evidence():
    record = {"object_id": "candidate_01", "provenance": {
        "support_status": "missing-evidence",
        "support_issues": ["source scene checksum is missing"],
        "evidence": [{
            "image_id": 10, "image_name": "a.png", "camera_id": 1,
            "instance_id": "a", "mask_path": "a.png", "mask_sha256": "a" * 64,
            "coordinate_space": "view",
        }],
    }}
    with pytest.raises(MissingSupportEvidence, match="marked 'missing-evidence'"):
        parse_support_observations(record)


def test_selected_view_uses_image_id_not_list_position(tmp_path):
    model = _model(ids=(101, 205, 999))
    observations = [SupportObservation(i, image.name, 1, "m.png", "a" * 64, instance_id=i)
                    for i, image in zip((999, 101, 205), (model.images[2], model.images[0], model.images[1]))]
    selected, indices = select_supporting_observations(model, observations, min_views=3)
    assert [item.image_id for item in selected] == [999, 101, 205]
    assert set(indices) == {0, 1, 2}


def test_unsupported_camera_rejected():
    model = _model(ids=(10, 20, 30), camera_ids=(1, 1, 1))
    observations = [SupportObservation(i, image.name, 7, "m.png", "a" * 64, instance_id=i)
                    for i, image in zip((10, 20, 30), model.images)]
    with pytest.raises(MissingSupportEvidence, match="camera mismatch"):
        select_supporting_observations(model, observations, min_views=3)


def test_mask_mapping_is_nearest_and_checksum_verified(tmp_path):
    model = _model(ids=(10, 20, 30))
    image_path = tmp_path / "m.png"
    source = np.zeros((8, 8), dtype=np.uint8)
    source[2:6, 2:6] = 255
    Image.fromarray(source).save(image_path)
    observation = SupportObservation(
        10, model.images[0].name, 1, "m.png", hashlib.sha256(image_path.read_bytes()).hexdigest(),
        instance_id="i", image_width=8, image_height=8, mask_width=8, mask_height=8,
        coordinate_space="rectified", transform={"output_size": [8, 8], "intrinsics": [[4, 0, 4], [0, 4, 4], [0, 0, 1]]},
    )
    view = View("cam/10.png", torch.zeros((8, 8, 3), dtype=torch.uint8),
                torch.eye(4), torch.tensor([[4., 0, 4], [0, 4, 4], [0, 0, 1]]), 1,
                    image_id=10, rectification_input_size=(8, 8),
                rectification_input_intrinsics=torch.tensor([[8., 0, 4], [0, 8, 4], [0, 0, 1]]))
    mask = load_mask_for_view(observation, tmp_path, view, model.cameras[1])
    assert mask.shape == (8, 8) and mask.dtype == bool
    assert mask[2:6, 2:6].all()
    assert not mask[0, 0]


def test_geometry_diagnostics_preserve_small_spatial_outlier():
    data = {
        "means": np.vstack([np.zeros((100, 3), np.float32) + np.array([0.01, 0.01, 0.01]),
                             np.array([[100, 100, 100]], np.float32)]),
        "scales": np.log(np.full((101, 3), 0.01, np.float32)),
        "quats": np.tile(np.array([[1, 0, 0, 0]], np.float32), (101, 1)),
        "opacities": np.zeros(101, np.float32),
    }
    report = inspect_splat_geometry(data, 10.0, object_id="x")
    assert report["diagnostic_outliers"] >= 1
    assert report["kept_count"] == 101


def _fake_views(count=3, size=8):
    views = []
    for index in range(count):
        views.append(View(
            f"cam/{index}.png", torch.zeros((size, size, 3), dtype=torch.uint8),
            torch.eye(4), torch.tensor([[4., 0, 4], [0, 4, 4], [0, 0, 1]]), 1,
            image_id=index + 10,
        ))
    class FakeViews:
        scene_scale = 10.0
        device = "cpu"
        def __init__(self): self.views = views
        def full(self, index, max_long_edge=None):
            v = self.views[index]
            return Batch(v.image.float() / 255, v.world_to_camera.unsqueeze(0),
                         v.intrinsics.unsqueeze(0), v.width, v.height, index)
    return FakeViews()


def _support(fake):
    masks = {index: np.ones((8, 8), dtype=bool) for index in range(3)}
    depths = {index: np.ones((8, 8), dtype=np.float32) for index in range(3)}
    observations = [SupportObservation(index + 10, f"cam/{index}.png", 1, "m.png", "a" * 64, instance_id=index)
                    for index in range(3)]
    return ObjectSupport("x", observations, [0, 1, 2], masks, 10.0,
                         min_consistent_views=2, depth_maps=depths,
                         view_shapes={0: (8, 8), 1: (8, 8), 2: (8, 8)})


def test_multiview_filter_requires_depth_consistency():
    fake = _fake_views()
    support = _support(fake)
    points = np.array([[0, 0, 1], [0, 0, 2]], np.float32)
    colours = np.ones((2, 3), np.float32)
    kept, _, diagnostics = filter_multiview_points(points, colours, support, fake)
    assert len(kept) == 1 and diagnostics["points_after_consistency"] == 1


def test_mesh_support_rejects_unsupported_face_interior():
    fake = _fake_views()
    support = _support(fake)
    support.masks = {index: np.ones((8, 8), dtype=bool) for index in range(3)}
    for mask in support.masks.values():
        mask[3:6, 3:6] = False
    # Two supported endpoints with a triangle crossing the masked hole.
    vertices = np.array([[-0.5, 0, 1], [0.5, 0, 1], [0, 0.5, 1]], np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    with pytest.raises(ObjectSupportError, match="triangle interiors"):
        validate_mesh_support(vertices, faces, support, fake)


def test_mesh_support_rejects_any_unsupported_vertex():
    fake = _fake_views()
    support = _support(fake)
    for mask in support.masks.values():
        mask[3:6, 3:6] = False
    vertices = np.array([[-0.5, 0, 1], [0.5, 0, 1], [0, 0, 1]], np.float32)
    faces = np.array([[0, 1, 2]], dtype=np.int64)
    with pytest.raises(ObjectSupportError, match="unsupported vertices"):
        validate_mesh_support(vertices, faces, support, fake)
