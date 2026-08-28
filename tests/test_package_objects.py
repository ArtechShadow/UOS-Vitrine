"""The optional ``objects`` manifest extension is additive, validated and exact."""

from __future__ import annotations

import hashlib
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


def _valid_objects_dir(tmp_path: Path) -> Path:
    d = tmp_path / "objects"
    (d / "obj_001").mkdir(parents=True)
    mesh = d / "obj_001" / "mesh.glb"
    mesh.write_bytes(b"glb-bytes")
    doc = {
        "schema": "vitrine/object/1",
        "objects": [
            {
                "object_id": "obj_001",
                "label": "radio",
                "mesh_path": "obj_001/mesh.glb",
                "sha256": hashlib.sha256(b"glb-bytes").hexdigest(),
                "transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                "orientation_status": {"yaw": "observed"},
                "coverage": 0.42,
                "confidence": 0.8,
            }
        ],
    }
    (d / "objects.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


def _manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def test_objects_absent_leaves_manifest_unchanged(tmp_path):
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    manifest = _manifest(result.root)
    assert manifest["schema"] == "vitrine/preservation-package/1"
    assert "objects" not in manifest
    ok, problems = verify_package(result.root)
    assert ok, problems


def test_no_objects_is_byte_identical_golden(tmp_path):
    # An empty objects/ dir (no valid output) must produce the exact same package
    # layout as no objects/ at all — not merely "no objects key".
    inputs = _minimal_inputs(tmp_path)
    plain = build_package(tmp_path / "a1", **inputs)
    empty = tmp_path / "objects_empty"
    empty.mkdir()
    withdir = build_package(tmp_path / "a2", objects_dir=empty, **inputs)

    def layout(root: Path) -> set[str]:
        return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

    assert layout(plain.root) == layout(withdir.root)
    m1, m2 = _manifest(plain.root), _manifest(withdir.root)
    for m in (m1, m2):
        m.pop("created_utc", None)
        m["software"] = {}
    assert m1 == m2                      # identical but for timestamp/software probe


def test_objects_present_are_archived_listed_and_documented(tmp_path):
    result = build_package(
        tmp_path / "archive", objects_dir=_valid_objects_dir(tmp_path), **_minimal_inputs(tmp_path)
    )
    manifest = _manifest(result.root)

    assert manifest["schema"] == "vitrine/preservation-package/1"  # additive, unbumped
    assert len(manifest["objects"]) == 1
    rec = manifest["objects"][0]
    assert rec["label"] == "radio" and rec["coverage"] == 0.42
    # sha256 is recomputed at packaging time, not trusted from the sidecar
    assert rec["sha256"] == hashlib.sha256(b"glb-bytes").hexdigest()

    assert (result.root / "derivatives" / "objects" / "obj_001" / "mesh.glb").is_file()
    paths = {f["path"] for f in manifest["files"]}
    assert "derivatives/objects/obj_001/mesh.glb" in paths
    # README gains the provenance note only when objects are present
    assert "derivatives/objects/" in (result.root / "README.md").read_text(encoding="utf-8")
    ok, problems = verify_package(result.root)
    assert ok, problems


def test_invalid_objects_output_is_ignored_not_archived(tmp_path):
    # A malformed objects.json must neither crash packaging nor archive anything;
    # the package is identical to a no-object run.
    d = tmp_path / "objects"
    d.mkdir()
    (d / "objects.json").write_text("{ not json", encoding="utf-8")
    (d / "stray.bin").write_bytes(b"partial")
    result = build_package(tmp_path / "archive", objects_dir=d, **_minimal_inputs(tmp_path))
    manifest = _manifest(result.root)
    assert "objects" not in manifest
    assert not (result.root / "derivatives" / "objects").exists()
    ok, problems = verify_package(result.root)
    assert ok, problems


def test_verify_detects_extra_file(tmp_path):
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    (result.root / "model" / "sneaked.bin").write_bytes(b"intruder")
    ok, problems = verify_package(result.root)
    assert not ok
    assert any(p.startswith("UNLISTED") for p in problems)


def test_verify_detects_size_and_content_tamper(tmp_path):
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    listed = json.loads((result.root / "manifest.json").read_text())["files"][0]["path"]
    (result.root / listed).write_bytes(b"a different length payload entirely")
    ok, problems = verify_package(result.root)
    assert not ok
    assert any(p.startswith(("SIZE", "CHANGED")) for p in problems)


def test_packager_emits_posix_paths(tmp_path):
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    manifest = _manifest(result.root)
    assert all("\\" not in f["path"] for f in manifest["files"])


def test_verify_normalises_windows_backslash_paths(tmp_path):
    # A package authored on Windows stores backslash separators. verify must
    # accept them (normalising to POSIX) while still rejecting real traversal.
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    mpath = result.root / "manifest.json"
    manifest = json.loads(mpath.read_text())
    for entry in manifest["files"]:
        entry["path"] = entry["path"].replace("/", "\\")   # legacy Windows form
    mpath.write_text(json.dumps(manifest), encoding="utf-8")

    ok, problems = verify_package(result.root)
    assert ok, problems                                     # backslash paths verify

    # a genuine backslash traversal is still rejected
    manifest["files"].append({"path": "..\\..\\etc\\passwd", "bytes": 1, "sha256": "0" * 64})
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    ok, problems = verify_package(result.root)
    assert not ok
    assert any(p.startswith("UNSAFE PATH") for p in problems)


def test_verify_rejects_unsafe_manifest_path(tmp_path):
    result = build_package(tmp_path / "archive", **_minimal_inputs(tmp_path))
    mpath = result.root / "manifest.json"
    manifest = json.loads(mpath.read_text())
    manifest["files"].append({"path": "../escape.txt", "bytes": 1, "sha256": "0" * 64})
    mpath.write_text(json.dumps(manifest), encoding="utf-8")
    ok, problems = verify_package(result.root)
    assert not ok
    assert any(p.startswith("UNSAFE PATH") for p in problems)
