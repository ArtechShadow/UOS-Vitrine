"""Deterministic checks for the A6000 rehearsal readiness record."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("vitrine_rehearsal_readiness", ROOT / "scripts" / "rehearsal_readiness.py")
READINESS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(READINESS)


def _stub_preflight(monkeypatch):
    monkeypatch.setattr(
        READINESS,
        "_preflight_checks",
        lambda *args, **kwargs: (
            [
                READINESS._check("Output folder", "pass", "writable"),
                READINESS._check("GPU", "pass", "NVIDIA RTX A6000"),
                READINESS._check("GPU driver", "pass", "580.00"),
                READINESS._check("GPU compute capability", "pass", "8.6"),
                READINESS._check("PyTorch CUDA", "pass", "12.8"),
                READINESS._check("Torch CUDA execution", "pass", "synchronised"),
                READINESS._check("gsplat CUDA extension", "pass", "loaded"),
            ],
            {"gpu_model": "NVIDIA RTX A6000", "compute_capability": "8.6"},
        ),
    )


def _tiny_glb(path: Path) -> None:
    document = {"asset": {"version": "2.0"}, "meshes": [{"primitives": [{}]}]}
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    payload += b" " * ((4 - len(payload) % 4) % 4)
    total = 12 + 8 + len(payload)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, total) + struct.pack("<I4s", len(payload), b"JSON") + payload)


def test_artifact_hashes_exclude_raw_originals_and_failed_generations(tmp_path):
    run = tmp_path / "run"
    (run / "model").mkdir(parents=True)
    (run / "archive" / "originals").mkdir(parents=True)
    (run / "object-meshes" / ".working-bad").mkdir(parents=True)
    (run / "model" / "scene.splat").write_bytes(b"x" * 32)
    (run / "model" / "progress.json").write_text("live", encoding="utf-8")
    (run / "archive" / "manifest.json").write_text("{}", encoding="utf-8")
    (run / "archive" / "originals" / "capture.mov").write_bytes(b"private raw media")
    (run / "object-meshes" / ".working-bad" / "mesh.glb").write_bytes(b"failed")

    hashes = READINESS.collect_artifact_hashes(run)

    assert set(hashes) == {"archive/manifest.json", "model/scene.splat"}
    assert hashes["model/scene.splat"]["sha256"] == hashlib.sha256(b"x" * 32).hexdigest()


def test_only_latest_published_mesh_generation_is_selected(tmp_path):
    run = tmp_path / "run"
    root = run / "object-meshes"
    generation = "a" * 32
    folder = root / "published" / generation
    folder.mkdir(parents=True)
    current = folder / "object-0001.glb"
    _tiny_glb(current)
    stale = root / "published" / ("b" * 32)
    stale.mkdir(parents=True)
    _tiny_glb(stale / "object-0001.glb")
    manifest = {
        "schema": "vitrine/object-mesh/1",
        "generation": generation,
        "objects": [{"glb_file": "object-0001.glb", "glb_sha256": READINESS.sha256_file(current)}],
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert READINESS._published_meshes(run) == [current.resolve()]


def test_changed_artifact_invalidates_explicit_previous_approval(tmp_path, monkeypatch):
    _stub_preflight(monkeypatch)
    run = tmp_path / "run"
    (run / "model").mkdir(parents=True)
    artifact = run / "model" / "scene.splat"
    artifact.write_bytes(b"a" * 32)
    output = run / "readiness.json"
    first = READINESS.build_readiness(project_root=tmp_path, run_dir=run, output=output)
    first["human_visual_approval"] = {
        "status": "approved",
        "approved": True,
        "reviewer": "human",
        "notes": "reviewed",
        "artifact_hashes": first["artifact_hashes"],
    }
    READINESS.write_report(first, output)
    artifact.write_bytes(b"b" * 32)

    second = READINESS.build_readiness(project_root=tmp_path, run_dir=run, output=output)

    approval = second["human_visual_approval"]
    assert approval["approved"] is False
    assert approval["status"] == "pending"
    assert approval["approval_invalidated"] is True


def test_approval_requires_hashed_output():
    with pytest.raises(ValueError, match="hashed output artefact"):
        READINESS._approval(None, {}, approve=True, reviewer="human", notes="checked")


def test_required_not_run_stage_blocks_report(tmp_path, monkeypatch):
    _stub_preflight(monkeypatch)
    run = tmp_path / "run"
    run.mkdir()

    report = READINESS.build_readiness(project_root=tmp_path, run_dir=run)

    assert report["status"] == "blocked"
    assert any(
        check["name"] == "Object sidecar contract" and check["status"] == "not_run"
        for check in report["reconstruction_quality"]
    )
    assert any(check["status"] == "not_run" for check in report["blocking_checks"])


def test_legacy_preflight_error_is_normalized_and_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        READINESS,
        "_preflight_checks",
        lambda *args, **kwargs: (
            [{"name": "gsplat CUDA extension", "status": "error", "detail": "extension failed", "optional": False}],
            {"gpu_model": "NVIDIA RTX A6000", "compute_capability": "8.6"},
        ),
    )

    report = READINESS.build_readiness(project_root=tmp_path, run_dir=tmp_path / "missing")

    check = next(item for item in report["real_gpu_execution"] if item["name"] == "gsplat CUDA extension")
    assert check["status"] == "fail"
    assert check["required"] is True
    assert any(item["name"] == "gsplat CUDA extension" for item in report["blocking_checks"])


def test_fresh_capture_check_reports_video_tools_without_source(monkeypatch, tmp_path):
    monkeypatch.setattr(READINESS, "_preflight_checks", lambda *args, **kwargs: (
        [
            {"name": "ffmpeg", "status": "error", "detail": "not found", "optional": True},
            {"name": "ffprobe", "status": "ok", "detail": "present", "optional": True},
            {"name": "Output folder", "status": "ok", "detail": "writable", "optional": False},
        ],
        {"gpu_model": "NVIDIA RTX A6000", "compute_capability": "8.6"},
    ))

    report = READINESS.build_readiness(project_root=tmp_path, run_dir=tmp_path / "missing")

    names = {item["name"] for item in report["software_checks"]}
    assert {"ffmpeg", "ffprobe"} <= names
    ffmpeg = next(item for item in report["software_checks"] if item["name"] == "ffmpeg")
    assert ffmpeg["status"] == "fail"
    assert ffmpeg["required"] is False


def test_target_label_and_actual_gpu_are_kept_separate(tmp_path, monkeypatch):
    _stub_preflight(monkeypatch)
    report = READINESS.build_readiness(project_root=tmp_path, hardware_target="high")

    assert report["target"]["hardware_target"] == "high"
    assert report["target"]["hardware"] == "NVIDIA RTX 5090"
    assert any(check["name"] == "Target GPU identity" for check in report["real_gpu_execution"])
    target = next(check for check in report["real_gpu_execution"] if check["name"] == "Hardware target")
    assert target["status"] == "fail"


def test_report_is_atomic_and_machine_readable(tmp_path, monkeypatch):
    _stub_preflight(monkeypatch)
    output = tmp_path / "nested" / "readiness.json"
    report = READINESS.build_readiness(project_root=tmp_path, output=output)
    READINESS.write_report(report, output)

    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["schema"] == READINESS.SCHEMA
    assert {"software_checks", "real_gpu_execution", "reconstruction_quality", "offline_operation"} <= loaded.keys()
    assert loaded["human_visual_approval"]["status"] == "pending"
