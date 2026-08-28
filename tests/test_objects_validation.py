"""The object-sidecar trust boundary rejects hostile and malformed output."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from vitrine.objects import (
    ObjectManifestError,
    load_validated_objects,
    safe_component,
    safe_copy_tree,
)


def _base(tmp: Path) -> tuple[Path, dict]:
    d = tmp / "objects"
    (d / "obj_001").mkdir(parents=True)
    mesh = d / "obj_001" / "mesh.glb"
    mesh.write_bytes(b"mesh-bytes")
    rec = {
        "object_id": "obj_001",
        "label": "radio",
        "mesh_path": "obj_001/mesh.glb",
        "sha256": hashlib.sha256(b"mesh-bytes").hexdigest(),
    }
    return d, {"schema": "vitrine/object/1", "objects": [rec]}


def _write(d: Path, doc: dict) -> Path:
    (d / "objects.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


def test_valid_output_projects_recomputed_hash(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"][0].update({"coverage": 0.4, "confidence": 0.8, "extra": "dropped"})
    records = load_validated_objects(_write(d, doc))
    assert len(records) == 1
    rec = records[0]
    assert rec["sha256"] == hashlib.sha256(b"mesh-bytes").hexdigest()
    assert rec["coverage"] == 0.4 and rec["confidence"] == 0.8
    assert "extra" not in rec               # only documented fields projected
    assert "transform" not in rec           # absent optional field omitted, not null


def test_missing_manifest(tmp_path):
    (tmp_path / "objects").mkdir()
    with pytest.raises(ObjectManifestError, match="no objects.json"):
        load_validated_objects(tmp_path / "objects")


def test_invalid_utf8(tmp_path):
    d, _ = _base(tmp_path)
    (d / "objects.json").write_bytes(b"\xff\xfe not utf-8")
    with pytest.raises(ObjectManifestError, match="UTF-8"):
        load_validated_objects(d)


def test_invalid_json(tmp_path):
    d, _ = _base(tmp_path)
    (d / "objects.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ObjectManifestError, match="JSON"):
        load_validated_objects(d)


def test_wrong_schema(tmp_path):
    d, doc = _base(tmp_path)
    doc["schema"] = "something/else"
    with pytest.raises(ObjectManifestError, match="schema"):
        load_validated_objects(_write(d, doc))


@pytest.mark.parametrize("objects", [None, 3, "x", {"a": 1}])
def test_objects_must_be_list(tmp_path, objects):
    d, doc = _base(tmp_path)
    doc["objects"] = objects
    with pytest.raises(ObjectManifestError, match="'objects' must be a list"):
        load_validated_objects(_write(d, doc))


def test_non_dict_entry(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"].append("not-an-object")
    with pytest.raises(ObjectManifestError, match=r"objects\[1\] must be an object"):
        load_validated_objects(_write(d, doc))


def test_missing_required_field(tmp_path):
    d, doc = _base(tmp_path)
    del doc["objects"][0]["label"]
    with pytest.raises(ObjectManifestError, match="missing required"):
        load_validated_objects(_write(d, doc))


@pytest.mark.parametrize("oid", ["../evil", "a/b", "a\\b", "id:reserved", "", "sp ace"])
def test_unsafe_object_id(tmp_path, oid):
    d, doc = _base(tmp_path)
    doc["objects"][0]["object_id"] = oid
    with pytest.raises(ObjectManifestError, match="object_id"):
        load_validated_objects(_write(d, doc))


def test_duplicate_object_id(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"].append(dict(doc["objects"][0]))
    with pytest.raises(ObjectManifestError, match="duplicate object_id"):
        load_validated_objects(_write(d, doc))


@pytest.mark.parametrize("mesh", ["/etc/passwd", "../../escape.glb", "..\\win.glb", "C:/abs.glb"])
def test_unsafe_mesh_path(tmp_path, mesh):
    d, doc = _base(tmp_path)
    doc["objects"][0]["mesh_path"] = mesh
    with pytest.raises(ObjectManifestError, match="mesh_path"):
        load_validated_objects(_write(d, doc))


def test_mesh_path_missing_file(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"][0]["mesh_path"] = "obj_001/gone.glb"
    doc["objects"][0]["sha256"] = "0" * 64
    with pytest.raises(ObjectManifestError, match="not a regular file"):
        load_validated_objects(_write(d, doc))


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_mesh_path_symlink_rejected(tmp_path):
    d, doc = _base(tmp_path)
    # symlink to a file *inside* the tree, so containment passes and the
    # is_symlink guard is what does the rejecting.
    real = d / "obj_001" / "real.glb"
    real.write_bytes(b"secret")
    link = d / "obj_001" / "link.glb"
    try:
        os.symlink(real, link)
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlink here")
    doc["objects"][0]["mesh_path"] = "obj_001/link.glb"
    doc["objects"][0]["sha256"] = hashlib.sha256(b"secret").hexdigest()
    with pytest.raises(ObjectManifestError, match="regular file"):
        load_validated_objects(_write(d, doc))


def test_duplicate_mesh_path(tmp_path):
    d, doc = _base(tmp_path)
    second = dict(doc["objects"][0])
    second["object_id"] = "obj_002"
    doc["objects"].append(second)
    with pytest.raises(ObjectManifestError, match="duplicate mesh_path"):
        load_validated_objects(_write(d, doc))


def test_hash_mismatch(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"][0]["sha256"] = "a" * 64
    with pytest.raises(ObjectManifestError, match="sha256 mismatch"):
        load_validated_objects(_write(d, doc))


def test_bad_sha_format(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"][0]["sha256"] = "nothex"
    with pytest.raises(ObjectManifestError, match="64 hex"):
        load_validated_objects(_write(d, doc))


@pytest.mark.parametrize("value", [-0.1, 1.5, float("nan"), float("inf"), "0.5"])
def test_confidence_out_of_range_or_non_finite(tmp_path, value):
    d, doc = _base(tmp_path)
    doc["objects"][0]["confidence"] = value
    with pytest.raises(ObjectManifestError, match="confidence"):
        load_validated_objects(_write(d, doc))


@pytest.mark.parametrize("transform", [
    [[1, 0, 0, 0]],                                   # wrong shape
    [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, "x"]],  # non-numeric
    [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, float("inf")]],  # non-finite
])
def test_malformed_transform(tmp_path, transform):
    d, doc = _base(tmp_path)
    doc["objects"][0]["transform"] = transform
    with pytest.raises(ObjectManifestError, match="transform"):
        load_validated_objects(_write(d, doc))


def test_empty_objects_list_is_valid(tmp_path):
    d, doc = _base(tmp_path)
    doc["objects"] = []
    assert load_validated_objects(_write(d, doc)) == []


def test_safe_component():
    assert safe_component("obj_001") and safe_component("a.b-c")
    assert not safe_component("../x") and not safe_component("a/b")
    assert not safe_component("") and not safe_component(None)


def test_safe_copy_tree_copies_regular_files(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a", encoding="utf-8")
    (src / "sub" / "b.txt").write_text("b", encoding="utf-8")
    n = safe_copy_tree(src, tmp_path / "dst")
    assert n == 2
    assert (tmp_path / "dst" / "sub" / "b.txt").read_text() == "b"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlink support")
def test_safe_copy_tree_rejects_symlink(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_text("x", encoding="utf-8")
    try:
        os.symlink(tmp_path / "outside.txt", src / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("cannot create symlink here")
    with pytest.raises(ObjectManifestError, match="symlink"):
        safe_copy_tree(src, tmp_path / "dst")
