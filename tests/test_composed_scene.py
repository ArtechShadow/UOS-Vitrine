"""The optional composed_scene manifest field (PR#2): validated like mesh_path."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from vitrine.objects import ObjectManifestError, load_validated_composed_scene
from vitrine.package import build_package, verify_package
from vitrine.serve import _objects_summary


def _inputs(tmp_path: Path) -> dict:
    src = tmp_path / "src"; src.mkdir(); (src / "a.jpg").write_bytes(b"img")
    sfm = tmp_path / "sfm"; sfm.mkdir(); (sfm / "cameras.txt").write_text("# c\n")
    model = tmp_path / "scene.ply"; model.write_bytes(b"ply")
    return {"originals": [src], "sfm_dir": sfm, "model_ply": model}


def _objects_dir(tmp_path: Path, *, with_scene=True, sha=None, path="composed_scene.glb") -> Path:
    d = tmp_path / "objects"
    (d / "obj_001").mkdir(parents=True)
    (d / "obj_001" / "mesh.glb").write_bytes(b"glb")
    doc = {
        "schema": "vitrine/object/1",
        "objects": [{"object_id": "obj_001", "label": "radio",
                     "mesh_path": "obj_001/mesh.glb",
                     "sha256": hashlib.sha256(b"glb").hexdigest()}],
    }
    if with_scene:
        (d / "composed_scene.glb").write_bytes(b"scene-glb")
        real = hashlib.sha256(b"scene-glb").hexdigest()
        doc["composed_scene"] = {"path": path, "sha256": sha or real}
    (d / "objects.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


def test_absent_composed_scene(tmp_path):
    assert load_validated_composed_scene(_objects_dir(tmp_path, with_scene=False)) is None


def test_valid_composed_scene_recomputes_hash(tmp_path):
    ref = load_validated_composed_scene(_objects_dir(tmp_path))
    assert ref["path"] == "composed_scene.glb"
    assert ref["sha256"] == hashlib.sha256(b"scene-glb").hexdigest()
    assert ref["derivative_class"] == "composed-derivative"


def test_hash_mismatch_rejected(tmp_path):
    with pytest.raises(ObjectManifestError, match="sha256 mismatch"):
        load_validated_composed_scene(_objects_dir(tmp_path, sha="0" * 64))


def test_unsafe_path_rejected(tmp_path):
    with pytest.raises(ObjectManifestError, match="composed_scene.path"):
        load_validated_composed_scene(_objects_dir(tmp_path, path="../escape.glb"))


def test_package_surfaces_and_archives_composed_scene(tmp_path):
    result = build_package(tmp_path / "archive", objects_dir=_objects_dir(tmp_path), **_inputs(tmp_path))
    manifest = json.loads((result.root / "manifest.json").read_text())
    assert manifest["composed_scene"]["path"] == "composed_scene.glb"
    assert manifest["composed_scene"]["derivative_class"] == "composed-derivative"
    assert (result.root / "derivatives" / "objects" / "composed_scene.glb").is_file()
    # README carries the composed-derivative caveat
    assert "composed derivative" in (result.root / "README.md").read_text().lower()
    ok, problems = verify_package(result.root)
    assert ok, problems


def test_package_without_scene_has_no_key(tmp_path):
    result = build_package(tmp_path / "archive", objects_dir=_objects_dir(tmp_path, with_scene=False),
                           **_inputs(tmp_path))
    manifest = json.loads((result.root / "manifest.json").read_text())
    assert "composed_scene" not in manifest


def test_serve_links_composed_scene(tmp_path):
    run = tmp_path / "runs" / "nc04"
    run.mkdir(parents=True)
    d = _objects_dir(run)              # writes run/objects/...
    summary = _objects_summary(run)
    assert summary["composed_scene"]["url"].endswith("objects/composed_scene.glb")
    assert summary["composed_scene"]["derivative_class"] == "composed-derivative"
