"""Capture-session contract: layout, hashes, and ingest staging."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from vitrine.capture_session import (
    SCHEMA,
    CaptureSessionError,
    import_session,
    stage_session,
    validate_session,
)
from vitrine.cli import build_parser, cmd_capture_session, cmd_ingest
from vitrine.ingest import classify_sources
from vitrine.package import build_package, verify_package


def _jpeg(path: Path, size: tuple[int, int] = (64, 48), colour: tuple[int, int, int] = (40, 50, 60)) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="JPEG")
    data = buf.getvalue()
    path.write_bytes(data)
    return data


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_entry(rel: str, data: bytes, role: str, group: str | None = None) -> dict:
    entry = {"path": rel, "bytes": len(data), "sha256": _sha(data), "role": role}
    if group:
        entry["camera_group"] = group
    return entry


def _document(files: list[dict], **overrides) -> dict:
    stills = [f for f in files if f["role"] == "still"]
    doc = {
        "schema": SCHEMA,
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "title": "Nested Cinema rehearsal",
        "subject": "Installation space",
        "device": {"model": "iPhone 15 Plus", "os": "iOS 18.1", "lidar_available": True, "cameras": ["wide"]},
        "locked_settings": {"ae": True, "awb": True, "af": True, "flash": "off", "zoom": 1.0},
        "screens_policy": "paused",
        "mirrors_policy": "accepted",
        "room_size": "room",
        "passes": [
            {"type": "orbit_chest", "still_count": len(stills)},
            {"type": "orbit_knee", "still_count": 0},
            {"type": "loop_close", "still_count": 0},
            {"type": "detail", "still_count": 0},
            {"type": "video", "still_count": 0},
        ],
        "checklist": {
            "three_or_more_views": True,
            "two_heights": True,
            "loop_closed": True,
            "detail_pass": True,
            "corners": True,
            "floor_edges": True,
            "exposure_locked": True,
        },
        "coverage": {
            "stills_count": len(stills),
            "stills_target": 72,
            "overlap_estimate": 0.7,
            "lidar_used": True,
            "holes": [],
        },
        "files": files,
    }
    doc.update(overrides)
    if "coverage" in overrides and "stills_count" not in overrides.get("coverage", {}):
        doc["coverage"]["stills_count"] = len(stills)
    return doc


def _write_session(root: Path, *, n_stills: int = 2, with_video: bool = True, doc_overrides: dict | None = None) -> Path:
    files: list[dict] = []
    for i in range(n_stills):
        rel = f"stills/wide/IMG_{i:04d}.jpg"
        data = _jpeg(root / rel, colour=(40 + i, 50, 60))
        files.append(_file_entry(rel, data, "still", "wide"))
    if with_video:
        rel = "video/glue.mov"
        data = b"ftypqt  fake-mov"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        files.append(_file_entry(rel, data, "video"))
    doc = _document(files, **(doc_overrides or {}))
    (root / "capture.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return root


def test_valid_session_loads(tmp_path):
    root = _write_session(tmp_path / "session")
    session = validate_session(root)
    assert session.session_id.endswith("eeeeeeeeeeee")
    assert len(session.stills) == 2
    assert len(session.videos) == 1
    assert session.warnings  # stills_target 72 vs 2 stills


def test_missing_manifest(tmp_path):
    (tmp_path / "session").mkdir()
    with pytest.raises(CaptureSessionError, match="no capture.json"):
        validate_session(tmp_path / "session")


def test_wrong_schema(tmp_path):
    root = _write_session(tmp_path / "session", doc_overrides={"schema": "other/1"})
    with pytest.raises(CaptureSessionError, match="schema"):
        validate_session(root)


def test_unlocked_ae_is_hard_error(tmp_path):
    root = _write_session(
        tmp_path / "session",
        doc_overrides={"locked_settings": {"ae": False, "awb": True, "af": True, "flash": "off", "zoom": 1.0}},
    )
    with pytest.raises(CaptureSessionError, match="locked_settings.ae"):
        validate_session(root)


def test_zoom_not_one_is_hard_error(tmp_path):
    root = _write_session(
        tmp_path / "session",
        doc_overrides={"locked_settings": {"ae": True, "awb": True, "af": True, "flash": "off", "zoom": 2.0}},
    )
    with pytest.raises(CaptureSessionError, match="zoom"):
        validate_session(root)


def test_missing_screens_policy(tmp_path):
    root = _write_session(tmp_path / "session")
    doc = json.loads((root / "capture.json").read_text(encoding="utf-8"))
    del doc["screens_policy"]
    (root / "capture.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CaptureSessionError, match="screens_policy"):
        validate_session(root)


def test_flattened_layout_rejected(tmp_path):
    root = tmp_path / "flat"
    data = _jpeg(root / "IMG_0001.jpg")
    files = [_file_entry("IMG_0001.jpg", data, "still", "wide")]
    doc = _document(files)
    # Point the listed path at stills so the document itself is consistent;
    # the on-disk file sits at the root, which is the flattened-layout case.
    doc["files"][0]["path"] = "stills/wide/IMG_0001.jpg"
    (root / "capture.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CaptureSessionError, match="flattened"):
        validate_session(root)


def test_mixed_video_in_stills_rejected(tmp_path):
    root = tmp_path / "mixed"
    still = _jpeg(root / "stills" / "wide" / "a.jpg")
    (root / "stills" / "wide" / "b.mov").write_bytes(b"mov")
    files = [_file_entry("stills/wide/a.jpg", still, "still", "wide")]
    (root / "capture.json").write_text(json.dumps(_document(files)), encoding="utf-8")
    with pytest.raises(CaptureSessionError, match="video file inside stills"):
        validate_session(root)


def test_hash_mismatch(tmp_path):
    root = _write_session(tmp_path / "session")
    doc = json.loads((root / "capture.json").read_text(encoding="utf-8"))
    doc["files"][0]["sha256"] = "0" * 64
    (root / "capture.json").write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(CaptureSessionError, match="hash mismatch"):
        validate_session(root)


def test_incomplete_checklist_warns(tmp_path):
    root = _write_session(
        tmp_path / "session",
        doc_overrides={
            "checklist": {
                "three_or_more_views": True,
                "two_heights": False,
                "loop_closed": False,
                "detail_pass": False,
                "corners": True,
                "floor_edges": True,
                "exposure_locked": True,
            }
        },
    )
    session = validate_session(root)
    joined = " ".join(session.warnings)
    assert "loop" in joined and "height" in joined and "detail" in joined


def test_stage_preserves_camera_groups(tmp_path):
    root = _write_session(tmp_path / "session")
    ultra = _jpeg(root / "stills" / "ultrawide" / "U_0001.jpg", size=(80, 60), colour=(9, 9, 9))
    doc = json.loads((root / "capture.json").read_text(encoding="utf-8"))
    doc["files"].append(_file_entry("stills/ultrawide/U_0001.jpg", ultra, "still", "ultrawide"))
    doc["coverage"]["stills_count"] = 3
    (root / "capture.json").write_text(json.dumps(doc), encoding="utf-8")
    # Sidecar must not be staged.
    (root / "sidecar" / "rejected").mkdir(parents=True)
    _jpeg(root / "sidecar" / "rejected" / "blur.jpg")

    session = validate_session(root)
    dest = tmp_path / "run" / "source"
    stage_session(session, dest)
    assert (dest / "stills" / "wide" / "IMG_0000.jpg").is_file()
    assert (dest / "stills" / "ultrawide" / "U_0001.jpg").is_file()
    assert (dest / "video" / "glue.mov").is_file()
    assert not (dest / "sidecar").exists()
    assert (tmp_path / "run" / "capture-session.json").is_file()
    groups = {g.name for g in classify_sources(dest)}
    assert "wide" in groups and "ultrawide" in groups


def test_import_zip_and_zip_slip(tmp_path):
    root = _write_session(tmp_path / "session")
    zpath = tmp_path / "session.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root).as_posix())
    run = tmp_path / "run"
    session = import_session(zpath, run)
    assert (run / "source" / "stills" / "wide" / "IMG_0000.jpg").is_file()
    assert session.title == "Nested Cinema rehearsal"

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.jpg", b"nope")
    with pytest.raises(CaptureSessionError, match="unsafe zip"):
        import_session(evil, tmp_path / "run2")


def test_cli_validate_and_ingest_session(tmp_path, capsys):
    root = _write_session(tmp_path / "session")
    parser = build_parser()
    args = parser.parse_args(["capture-session", "validate", str(root)])
    assert args.func is cmd_capture_session
    assert cmd_capture_session(args) == 0
    out = capsys.readouterr().out
    assert "valid" in out.lower()
    assert "warning" in out.lower()

    stills_only = _write_session(tmp_path / "stills-session", with_video=False)
    run_dir = tmp_path / "run"
    ingest_args = parser.parse_args([
        "--run-dir", str(run_dir),
        "ingest",
        "--session", str(stills_only),
        "--stills-budget", "10",
        "--video-budget", "10",
    ])
    assert cmd_ingest(ingest_args) == 0
    assert (run_dir / "source" / "stills" / "wide" / "IMG_0000.jpg").is_file()
    assert (run_dir / "ingest" / "ingest.json").is_file()


def test_package_copies_capture_session(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"image-bytes")
    sfm = tmp_path / "sfm"
    sfm.mkdir()
    (sfm / "cameras.txt").write_text("# camera\n", encoding="utf-8")
    model = tmp_path / "scene.ply"
    model.write_bytes(b"ply-bytes")
    session_path = tmp_path / "capture-session.json"
    session_path.write_text(json.dumps({
        "schema": SCHEMA,
        "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "screens_policy": "paused",
        "mirrors_policy": "accepted",
    }), encoding="utf-8")
    result = build_package(
        tmp_path / "archive",
        originals=[src],
        sfm_dir=sfm,
        model_ply=model,
        capture_session_path=session_path,
    )
    archived = result.root / "capture-session.json"
    assert archived.is_file()
    manifest = json.loads((result.root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["capture_session"]["screens_policy"] == "paused"
    ok, problems = verify_package(result.root)
    assert ok, problems
