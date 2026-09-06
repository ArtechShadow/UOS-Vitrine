"""The dashboard objects summary never builds a served path from an unsafe id."""

from __future__ import annotations

import json
from pathlib import Path

from vitrine.serve import _object_workflow, _objects_summary


def _write(run_dir: Path, records: list[dict]) -> None:
    (run_dir / "objects").mkdir(parents=True)
    (run_dir / "objects" / "objects.json").write_text(
        json.dumps({"schema": "vitrine/object/1", "objects": records}), encoding="utf-8"
    )


def test_absent_output_returns_none(tmp_path):
    assert _objects_summary(tmp_path) is None


def test_safe_id_with_thumbnail(tmp_path):
    _write(tmp_path, [{"object_id": "obj_001", "label": "radio", "confidence": 0.7}])
    thumb = tmp_path / "objects" / "obj_001" / "turntable"
    thumb.mkdir(parents=True)
    (thumb / "view_00.png").write_bytes(b"png")
    summary = _objects_summary(tmp_path)
    assert summary["count"] == 1
    item = summary["objects"][0]
    assert item["object_id"] == "obj_001"
    assert item["thumb_url"] == "/files/%s/objects/obj_001/turntable/view_00.png" % tmp_path.name


def test_traversal_id_is_neutralised(tmp_path):
    _write(tmp_path, [{"object_id": "../../etc/passwd", "label": "evil"}])
    item = _objects_summary(tmp_path)["objects"][0]
    assert item["object_id"] is None        # unsafe id not echoed as a path
    assert item["thumb_url"] is None         # and no served path built from it


def test_missing_thumbnail_gives_no_url(tmp_path):
    _write(tmp_path, [{"object_id": "obj_002", "label": "sofa"}])
    item = _objects_summary(tmp_path)["objects"][0]
    assert item["object_id"] == "obj_002" and item["thumb_url"] is None


def test_summary_links_existing_mesh(tmp_path):
    mesh = tmp_path / "objects" / "obj_003" / "mesh.glb"
    mesh.parent.mkdir(parents=True)
    mesh.write_bytes(b"glb")
    (tmp_path / "objects" / "objects.json").write_text(json.dumps({
        "schema": "vitrine/object/1",
        "objects": [{"object_id": "obj_003", "label": "radio", "mesh_path": "obj_003/mesh.glb"}],
    }), encoding="utf-8")
    item = _objects_summary(tmp_path)["objects"][0]
    assert item["mesh_name"] == "mesh.glb"
    assert item["mesh_url"].endswith("/objects/obj_003/mesh.glb")


def test_object_workflow_reports_real_model_inputs(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    (model / "scene.ply").write_bytes(b"ply")
    (model / "scene.splat").write_bytes(b"splat")
    monkeypatch.setenv("VITRINE_OBJECT_SIDECAR", "sidecar")
    workflow = _object_workflow(tmp_path)
    assert workflow["configured"] is True
    assert workflow["ready"] is True
    assert [item["name"] for item in workflow["inputs"]] == ["scene.ply", "scene.splat"]


def test_object_workflow_requires_a_3d_output(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    workflow = _object_workflow(tmp_path)
    assert workflow["configured"] is False
    assert workflow["ready"] is False
    assert workflow["inputs"] == []
