"""The optional ``objects`` manifest extension is additive and lossless."""

from __future__ import annotations

import json
from pathlib import Path

from vitrine.package import build_package, verify_package


def _minimal_inputs(tmp_path: Path) -> dict:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"image-bytes")
    sfm = tmp_path / "sfm"
    sfm.mkdir()
    (sfm / "cameras.txt").write_text("# camera\n", encoding="utf-8")
    model = tmp_path / "scene.ply"
    model.write_bytes(b"ply-bytes")
    return {"originals": [src], "sfm_dir": sfm, "model_ply": model}


def _objects_dir(tmp_path: Path) -> Path:
    d = tmp_path / "objects"
    (d / "obj_001").mkdir(parents=True)
    (d / "obj_001" / "mesh.glb").write_bytes(b"glb-bytes")
    doc = {
        "schema": "vitrine/object/1",
        "objects": [
            {
                "object_id": "obj_001",
                "label": "radio",
                "mesh_path": "obj_001/mesh.glb",
                "sha256": "0" * 64,
                "transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                "orientation_status": {"yaw": "observed"},
                "coverage": 0.42,
                "confidence": 0.8,
                "extra_field": "ignored",
            }
        ],
    }
    (d / "objects.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


def test_objects_absent_leaves_manifest_unchanged(tmp_path):
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    manifest = json.loads((result.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "vitrine/preservation-package/1"
    assert "objects" not in manifest
    ok, problems = verify_package(result.root)
    assert ok, problems


def test_objects_present_are_archived_and_listed(tmp_path):
    result = build_package(
        tmp_path / "archive", objects_dir=_objects_dir(tmp_path), **_minimal_inputs(tmp_path)
    )
    manifest = json.loads((result.root / "manifest.json").read_text(encoding="utf-8"))

    # schema is unchanged; the key is purely additive
    assert manifest["schema"] == "vitrine/preservation-package/1"
    assert len(manifest["objects"]) == 1
    rec = manifest["objects"][0]
    assert rec["label"] == "radio"
    assert rec["coverage"] == 0.42
    assert rec["confidence"] == 0.8
    assert "extra_field" not in rec  # only the documented projection is carried

    # the asset tree is archived under derivatives/objects/ and checksummed
    assert (result.root / "derivatives" / "objects" / "obj_001" / "mesh.glb").is_file()
    paths = {f["path"] for f in manifest["files"]}
    assert "derivatives/objects/obj_001/mesh.glb" in paths
    ok, problems = verify_package(result.root)
    assert ok, problems


def test_unreadable_objects_json_is_tolerated(tmp_path):
    d = tmp_path / "objects"
    d.mkdir()
    (d / "objects.json").write_text("{ not json", encoding="utf-8")
    (d / "keep.txt").write_text("asset", encoding="utf-8")
    result = build_package(tmp_path / "archive", objects_dir=d, **_minimal_inputs(tmp_path))
    manifest = json.loads((result.root / "manifest.json").read_text(encoding="utf-8"))
    # bad manifest -> no objects key, but the assets are still archived
    assert "objects" not in manifest
    assert (result.root / "derivatives" / "objects" / "keep.txt").is_file()
