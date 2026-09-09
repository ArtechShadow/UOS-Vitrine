"""Control-plane smoke tests; real media checks are opt-in via VITRINE_REAL_ROOT."""
import json
import os
from pathlib import Path

import pytest

from vitrine.engines import get_engine, LocalMeshEngine, validate_glb
from vitrine.pipeline import check_cancel, restore_args, run_lock
from vitrine.preflight import check_preflight
from vitrine.telemetry import measure, timing_summary


def test_gsplat_provider_loads_without_importing_training():
    engine = get_engine()
    assert engine.name == "gsplat"
    with pytest.raises(ValueError, match="Unknown reconstruction engine"):
        get_engine("not-installed")


def test_unconfigured_mesh_provider_has_actionable_error():
    with pytest.raises(ValueError, match="nonempty JSON string array"):
        LocalMeshEngine([])
    with pytest.raises(ValueError, match="executable is missing"):
        LocalMeshEngine(["vitrine-definitely-uninstalled-provider"])


def test_cancel_and_exclusive_ownership(tmp_path):
    with run_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already has a running"):
            with run_lock(tmp_path):
                pass
        check_cancel(tmp_path)
        (tmp_path / "cancel.request").touch()
        with pytest.raises(KeyboardInterrupt, match="Cancellation requested"):
            check_cancel(tmp_path)
    with run_lock(tmp_path):
        pass


def test_failed_stage_has_durable_timing(tmp_path):
    with pytest.raises(ValueError, match="test failure"):
        with measure(tmp_path, "preflight"):
            raise ValueError("test failure")
    event = timing_summary(tmp_path)[0]
    assert event["state"] == "failed" and event["seconds"] >= 0
    assert event["error"] == "ValueError: test failure"
    assert event["cpu_scope"] == "python-process"


def test_resume_uses_saved_configuration(tmp_path):
    from argparse import Namespace
    (tmp_path / "pipeline.json").write_text(json.dumps({"schema": "vitrine/pipeline/1",
        "config": {"quality": "demo", "source": "capture-input", "selection_preset": "balanced"}}))
    args = restore_args(Namespace(run_dir=str(tmp_path), resume=True, quality="archive"))
    assert args.quality == "demo" and args.selection_preset == "balanced"


def test_preflight_missing_input_is_actionable(tmp_path):
    result = check_preflight(tmp_path / "does-not-exist", tmp_path, require_gpu=False,
                             check_sfm=False, check_training=False)
    assert not result["ready"]
    failure = next(c for c in result["checks"] if c["name"] == "Input media")
    assert failure["status"] == "error" and failure["fix"]


def test_real_image_preflight(tmp_path):
    import shutil
    root = os.environ.get("VITRINE_REAL_ROOT")
    if not root:
        pytest.skip("Set VITRINE_REAL_ROOT to verify actual capture media")
    from vitrine.ingest import IMAGE_SUFFIXES
    source = next(p for p in (Path(root)/"source/stills").rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
    folder = tmp_path / "source"
    folder.mkdir()
    shutil.copy2(source, folder / source.name)
    result = check_preflight(folder, tmp_path, require_gpu=False, check_sfm=False, check_training=False)
    assert result["ready"], result


def test_invalid_glb_is_not_published(tmp_path):
    candidate = tmp_path / "incomplete.glb"
    candidate.write_bytes(b"glTF")
    with pytest.raises(ValueError, match="incomplete"):
        validate_glb(candidate)


def test_glb_export_preserves_real_mesh_geometry(tmp_path):
    root = os.environ.get("VITRINE_REAL_ROOT")
    if not root:
        pytest.skip("Set VITRINE_REAL_ROOT for real Polycam geometry")
    import struct
    import numpy as np
    from plyfile import PlyData, PlyElement
    from vitrine.mesh_glb import write_mesh_glb
    source = Path(root)/"runs/nested-cinema-04-master/archive/originals/05_08_2026.glb"
    if not source.is_file():
        pytest.skip("Preserved Polycam mesh is unavailable")
    with source.open("rb") as handle:
        handle.read(12)
        size, _ = struct.unpack("<I4s", handle.read(8))
        doc = json.loads(handle.read(size))
        size, kind = struct.unpack("<I4s", handle.read(8))
        assert kind == b"BIN\0"
        binary = handle.read(size)
    primitive = doc["meshes"][0]["primitives"][0]
    def read_accessor(index, dtype, columns):
        accessor = doc["accessors"][index]
        view = doc["bufferViews"][accessor["bufferView"]]
        offset = view.get("byteOffset", 0)+accessor.get("byteOffset", 0)
        return np.frombuffer(binary, dtype=dtype, count=accessor["count"]*columns,
                             offset=offset).reshape(-1, columns).copy()
    positions = read_accessor(primitive["attributes"]["POSITION"], "<f4", 3)
    indices = read_accessor(primitive["indices"], "<u2", 1).reshape(-1, 3)
    vertices = np.empty(len(positions), dtype=[("x", "f4"), ("y", "f4"), ("z", "f4")])
    for axis, name in enumerate(("x", "y", "z")):
        vertices[name] = positions[:, axis]
    faces = np.empty(len(indices), dtype=[("vertex_indices", "i4", (3,))])
    faces["vertex_indices"] = indices
    ply = tmp_path / "real-mesh.ply"
    PlyData([PlyElement.describe(vertices, "vertex"), PlyElement.describe(faces, "face")]).write(ply)
    output = write_mesh_glb(ply, tmp_path / "mesh.glb")
    exported = validate_glb(output)
    assert exported["accessors"][0]["count"] == len(positions)
    assert exported["accessors"][-1]["count"] == indices.size
    assert exported["accessors"][0]["min"] == positions.min(axis=0).tolist()
    assert exported["accessors"][0]["max"] == positions.max(axis=0).tolist()
