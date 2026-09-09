from pathlib import Path

import numpy as np
import pytest
from plyfile import PlyData, PlyElement

from vitrine.cleanup import cleanup_splat


def _master(path: Path, count: int = 20) -> np.ndarray:
    dtype = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("f_dc_0", "f4"), ("f_rest_0", "f4"),
        ("opacity", "f4"), ("scale_0", "f4"), ("rot_0", "f4"),
    ]
    data = np.zeros(count, dtype=dtype)
    data["x"] = np.arange(count, dtype=np.float32)
    data["f_dc_0"] = np.arange(count, dtype=np.float32) * 2
    data["f_rest_0"] = 7
    probabilities = np.linspace(0.1, 0.9, count)
    data["opacity"] = np.log(probabilities / (1 - probabilities))
    PlyData([PlyElement.describe(data, "vertex")], comments=["master"]).write(str(path))
    return data


def test_cleanup_derivative_preserves_master_and_records_hashes(tmp_path):
    source = tmp_path / "scene.ply"
    # One corrupt position and one inactive opacity; the rest exceed the
    # default minimum active population.
    loaded = _master(tmp_path / "initial.ply")
    loaded["x"][0] = np.nan
    loaded["opacity"][1] = -20
    with source.open("wb") as handle:
        PlyData([PlyElement.describe(loaded, "vertex")], comments=["master"]).write(handle)
    original_bytes = source.read_bytes()

    report = cleanup_splat(source, tmp_path / "candidate.ply")

    assert source.read_bytes() == original_bytes
    assert report.input_count == 20
    assert report.retained_count == 18
    assert report.dropped_nonfinite == 1
    assert report.dropped_low_opacity == 1
    assert Path(report.manifest).is_file()
    output = PlyData.read(report.target)["vertex"].data
    assert "f_rest_0" in (output.dtype.names or ())
    assert np.all(output["f_rest_0"] == 7)


def test_cleanup_has_no_default_spatial_cull_and_refuses_overwrite(tmp_path):
    source = tmp_path / "scene.ply"
    _master(source)
    candidate = tmp_path / "candidate.ply"

    report = cleanup_splat(source, candidate)

    assert report.retained_count == report.input_count
    with pytest.raises(FileExistsError):
        cleanup_splat(source, candidate)
    with pytest.raises(ValueError, match="differ"):
        cleanup_splat(source, source)


def test_cleanup_scene_relative_bound_is_explicit(tmp_path):
    source = tmp_path / "scene.ply"
    _master(source)

    report = cleanup_splat(source, tmp_path / "bounded.ply", scene_scale=2, radius_multiple=2, min_active=5)

    assert report.scene_scale == 2
    assert report.radius_multiple == 2
    assert report.dropped_outside_bounds > 0
