"""Deterministic save, provenance and worker-recovery contracts.

These fixtures exercise the software boundaries only.  They do not claim that
the resulting tiny Gaussian clouds represent a real capture or pass visual
acceptance on the XR Lab workstation.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

from vitrine import ply
from vitrine.construction import atomic_json, construction_payload, recover_stale_progress
from vitrine.pipeline import (
    PIPELINE_COMPATIBILITY,
    _invalidate_incompatible_stages,
    _recover_running_stages,
    current_compatibility,
    fingerprint,
    source_identity,
)
from vitrine.telemetry import measure, timing_summary
from vitrine.train import TrainReport, _update_training_save_state, save_master


def _cloud(count: int = 5, degree: int = 2) -> dict:
    rng = np.random.default_rng(2042)
    return {
        "means": rng.normal(size=(count, 3)).astype(np.float32),
        "scales": np.log(rng.uniform(0.02, 0.2, size=(count, 3))).astype(np.float32),
        "quats": np.tile(np.array([1, 0, 0, 0], dtype=np.float32), (count, 1)),
        "opacities": rng.normal(size=count).astype(np.float32),
        "sh0": rng.normal(size=(count, 1, 3)).astype(np.float32),
        "shN": rng.normal(size=(count, (degree + 1) ** 2 - 1, 3)).astype(np.float32),
        "sh_degree": degree,
    }


def test_full_sh_round_trip_has_verified_geometry_coefficients_and_checksum(tmp_path):
    cloud = _cloud()
    path = tmp_path / "scene.ply"

    ply.write_splat_ply(path, **cloud)
    receipt = ply.verify_splat_ply(path, expected_count=5, expected_sh_degree=2, require_nonempty=True)
    loaded = ply.read_splat_ply(path)

    assert receipt["count"] == loaded["count"] == 5
    assert receipt["sh_degree"] == loaded["sh_degree"] == 2
    assert receipt["sha256"] == ply.sha256_file(path)
    np.testing.assert_allclose(loaded["means"], cloud["means"])
    np.testing.assert_allclose(loaded["sh0"], cloud["sh0"])
    np.testing.assert_allclose(loaded["shN"], cloud["shN"])


def test_atomic_ply_promotion_preserves_previous_master_on_replace_failure(tmp_path, monkeypatch):
    cloud = _cloud(count=3, degree=1)
    target = tmp_path / "scene.ply"
    ply.write_splat_ply(target, **cloud)
    previous = target.read_bytes()

    def fail_replace(source, destination):
        raise OSError("injected promotion failure")

    monkeypatch.setattr(ply.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected promotion failure"):
        ply.write_splat_ply(target, **cloud)

    assert target.read_bytes() == previous
    assert not list(tmp_path.glob("scene.ply.writing-*"))


def test_nonfinite_generation_is_rejected_without_touching_master(tmp_path):
    cloud = _cloud(count=3, degree=1)
    target = tmp_path / "scene.ply"
    ply.write_splat_ply(target, **cloud)
    previous = target.read_bytes()
    broken = dict(cloud, means=cloud["means"].copy())
    broken["means"][1, 2] = np.nan

    with pytest.raises(ValueError, match="non-finite"):
        ply.write_splat_ply(target, **broken)
    assert target.read_bytes() == previous


def test_save_master_records_verified_receipt_and_evaluation_can_follow(tmp_path, monkeypatch):
    cloud = _cloud(count=4, degree=1)
    # Use the real writer through the save boundary; ``torch`` is only needed
    # for Parameter annotations at runtime, so small CPU tensors are enough.
    import torch

    params = {name: torch.nn.Parameter(torch.from_numpy(value)) for name, value in cloud.items()
              if name in {"means", "scales", "quats", "opacities", "sh0", "shN"}}
    path, artifact = save_master(params, tmp_path, 1, 2.0, generation="test-generation")
    state = json.loads((tmp_path / "save-state.json").read_text())

    assert path == tmp_path / "scene.ply"
    assert state["state"] == "master_saved"
    assert state["generation"] == "test-generation"
    assert state["verified"] is True
    assert state["artifact"]["sha256"] == artifact["sha256"]
    assert ply.verify_splat_ply(path, expected_sha256=artifact["sha256"], require_nonempty=True)


def test_final_save_failure_records_failure_and_keeps_last_known_good(tmp_path, monkeypatch):
    cloud = _cloud(count=3, degree=1)
    import torch

    params = {name: torch.nn.Parameter(torch.from_numpy(value)) for name, value in cloud.items()
              if name in {"means", "scales", "quats", "opacities", "sh0", "shN"}}
    target = tmp_path / "scene.ply"
    ply.write_splat_ply(target, **cloud)
    previous = target.read_bytes()

    def fail_save(*args, **kwargs):
        raise OSError("injected final-save failure")

    monkeypatch.setattr("vitrine.train._write", fail_save)
    with pytest.raises(OSError, match="injected final-save failure"):
        save_master(params, tmp_path, 1, 2.0, generation="failed-generation")

    state = json.loads((tmp_path / "save-state.json").read_text())
    assert state["state"] == "save_failed"
    assert state["verified"] is False
    assert "injected final-save failure" in state["error"]
    assert target.read_bytes() == previous


def test_save_failure_clears_current_receipt_but_retains_previous_evidence(tmp_path, monkeypatch):
    """A replacement save cannot leave an old receipt looking current."""
    import torch

    first = _cloud(count=3, degree=1)
    params = {name: torch.nn.Parameter(torch.from_numpy(value)) for name, value in first.items()
              if name in {"means", "scales", "quats", "opacities", "sh0", "shN"}}
    _, previous_artifact = save_master(params, tmp_path, 1, 2.0, generation="known-good")
    previous_bytes = (tmp_path / "scene.ply").read_bytes()
    observed = {}

    def fail_save(*args, **kwargs):
        observed.update(json.loads((tmp_path / "save-state.json").read_text()))
        raise OSError("injected replacement failure")

    monkeypatch.setattr("vitrine.train._write", fail_save)
    with pytest.raises(OSError, match="injected replacement failure"):
        save_master(params, tmp_path, 1, 2.0, generation="failed-replacement")

    assert observed["state"] == "saving"
    assert observed["verified"] is False
    assert observed["artifact"] is None
    assert observed["previous_artifact"]["sha256"] == previous_artifact["sha256"]
    state = json.loads((tmp_path / "save-state.json").read_text())
    assert state["state"] == "save_failed"
    assert state["verified"] is False
    assert state["artifact"] is None
    assert state["previous_artifact"]["sha256"] == previous_artifact["sha256"]
    assert (tmp_path / "scene.ply").read_bytes() == previous_bytes


def test_construction_payload_does_not_trust_unmarked_training_output(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "train.json").write_text(json.dumps({"state": "complete"}), encoding="utf-8")
    (model / "scene.ply").write_bytes(b"stale output")

    payload = construction_payload(tmp_path)

    assert payload["master_saved"] is False
    assert payload["master_artifact"] is None
    assert payload["done"]["train"] is False


def test_construction_payload_requires_stage_fingerprint_for_pipeline_completion(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    (model / "train.json").write_text(json.dumps({"state": "complete"}), encoding="utf-8")
    (model / "scene.ply").write_bytes(b"stale output")
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "schema": "vitrine/pipeline/1",
        "state": "complete",
        "stages": {"train": {"state": "complete"}},
    }), encoding="utf-8")

    payload = construction_payload(tmp_path)

    assert payload["state"] == "unknown"
    assert payload["done"]["train"] is False
    assert "fingerprint" in payload["error"]


def test_construction_payload_accepts_matching_verified_train_stage(tmp_path):
    import torch

    model = tmp_path / "model"
    model.mkdir()
    cloud = _cloud(count=2, degree=1)
    params = {name: torch.nn.Parameter(torch.from_numpy(value)) for name, value in cloud.items()
              if name in {"means", "scales", "quats", "opacities", "sh0", "shN"}}
    save_master(params, model, 1, 2.0, generation="known-good")
    (model / "train.json").write_text(json.dumps({"state": "complete"}), encoding="utf-8")
    outputs = fingerprint(tmp_path, "train")
    (tmp_path / "pipeline.json").write_text(json.dumps({
        "schema": "vitrine/pipeline/1",
        "state": "complete",
        "stages": {"train": {"state": "complete", "outputs": outputs}},
    }), encoding="utf-8")

    payload = construction_payload(tmp_path)

    assert payload["state"] == "complete"
    assert payload["master_saved"] is True
    assert payload["done"]["train"] is True


def test_stale_worker_is_explicitly_unknown_and_durable(tmp_path):
    now = time.time()
    atomic_json(tmp_path / "construction-status.json", {
        "schema": "vitrine/construction-status/1",
        "stage": "train",
        "state": "running",
        "pid": 2_147_483_647,
        "started": now - 120,
        "heartbeat": now - 120,
    })

    status = recover_stale_progress(tmp_path, now=now, stale_after=30)
    persisted = json.loads((tmp_path / "construction-status.json").read_text())
    assert status["state"] == persisted["state"] == "unknown"
    assert status["failure_kind"] == "worker_disappeared"
    assert "worker process is no longer alive" in status["error"]


def test_live_worker_with_recent_status_is_not_marked_unknown(tmp_path):
    now = time.time()
    state = {
        "stages": {"train": {"state": "running", "pid": os.getpid(),
                               "started": now - 120, "heartbeat": now - 1}}
    }
    assert _recover_running_stages(state, tmp_path, now=now) == []
    assert state["stages"]["train"]["state"] == "running"


def test_pipeline_fingerprint_rejects_truncated_master(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    cloud = _cloud(count=2, degree=1)
    ply.write_splat_ply(model / "scene.ply", **cloud)
    (model / "train.json").write_text("{}")
    data = (model / "scene.ply").read_bytes()
    (model / "scene.ply").write_bytes(data[:-7])

    with pytest.raises(ValueError, match="incomplete|trailing|payload"):
        fingerprint(tmp_path, "train")


def test_pipeline_refuses_resume_when_compatibility_is_unknown():
    state = {
        "compatibility": {"source_identity": "legacy-mtime-v1"},
        "stages": {"ingest": {"state": "complete"}},
    }
    reason = _invalidate_incompatible_stages(state)
    assert reason and state["stages"]["ingest"]["state"] == "complete"
    assert current_compatibility() != PIPELINE_COMPATIBILITY


def test_timing_record_contains_memory_and_disk_diagnostics(tmp_path):
    with measure(tmp_path, "save", bytes_written=12):
        pass
    event = timing_summary(tmp_path)[0]
    assert event["state"] == "complete"
    assert "memory_before" in event and "memory_after" in event
    assert "disk_before" in event and "disk_after" in event


def test_source_identity_uses_content_hash_and_ignores_mtime(tmp_path):
    source = tmp_path / "capture"
    source.mkdir()
    frame = source / "frame 01.jpg"
    frame.write_bytes(b"stable capture bytes")
    first = source_identity(source)
    os.utime(frame, (time.time() + 10, time.time() + 10))
    assert source_identity(source) == first
    frame.write_bytes(b"changed capture bytes")
    assert source_identity(source) != first


def test_train_report_serialises_nonfinite_metrics_as_explicit_null():
    report = TrainReport(
        profile="test", iterations=1, n_train_views=1, n_eval_views=0,
        n_gaussians=1, scene_scale=1.0, sh_degree=0, minutes=0.0,
        peak_vram_gb=0.0, final_psnr=float("nan"), final_ssim=float("nan"),
    )
    payload = json.loads(report.to_json())
    assert payload["final_psnr"] is None and payload["final_ssim"] is None


def test_ingest_fingerprint_detects_same_size_same_timestamp_changes(tmp_path):
    from vitrine.pipeline import fingerprint
    root = tmp_path / 'ingest'
    (root / 'images').mkdir(parents=True)
    (root / 'ingest.json').write_text('{}')
    image = root / 'images' / 'fixture.jpg'
    image.write_bytes(b'aaaa')
    initial = fingerprint(tmp_path, 'ingest')
    stamp = image.stat().st_mtime_ns
    image.write_bytes(b'bbbb')
    os.utime(image, ns=(stamp, stamp))
    assert fingerprint(tmp_path, 'ingest') != initial
