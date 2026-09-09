"""Hardware discovery, profile boundaries, and view-cache RAM preflight."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image as PILImage

from vitrine import hardware, profiles
from vitrine.colmap_io import Camera, Image, Model
from vitrine.dataset import ViewSet, estimate_cache_bytes, ram_guard_report


def _small_model(name: str = "view.jpg") -> Model:
    camera = Camera(
        id=1,
        model="PINHOLE",
        width=100,
        height=50,
        params={"fx": 80.0, "fy": 80.0, "cx": 50.0, "cy": 25.0},
    )
    image = Image(
        id=1,
        name=name,
        camera_id=1,
        qvec=np.array([1.0, 0.0, 0.0, 0.0]),
        tvec=np.zeros(3),
    )
    return Model(
        cameras={1: camera},
        images=[image],
        points_xyz=np.zeros((0, 3), dtype=np.float32),
        points_rgb=np.zeros((0, 3), dtype=np.float32),
    )


def test_hardware_detection_without_cuda(monkeypatch):
    monkeypatch.setattr(hardware, "_probe_nvidia_smi", dict)
    monkeypatch.setattr(hardware, "_probe_torch", lambda: {"torch_cuda_available": None})

    snapshot = hardware.detect_hardware(probe=True)

    assert snapshot["schema"] == hardware.SCHEMA
    assert snapshot["gpu_available"] is False
    assert snapshot["cuda_available"] is False
    assert snapshot["capability_tier"] == "cpu"
    assert snapshot["cpu_logical_cores"] >= 1
    assert "ram_total_gb" in snapshot


def test_json_environment_override_is_validated(monkeypatch):
    monkeypatch.setenv(
        hardware.OVERRIDE_ENV,
        json.dumps(
            {
                "gpu_available": True,
                "cuda_available": True,
                "gpu_vendor": "NVIDIA",
                "gpu_model": "NVIDIA RTX A6000",
                "vram_total_gb": 48.0,
                "vram_free_gb": 44.0,
                "compute_capability": "8.6",
            }
        ),
    )

    snapshot = hardware.detect_hardware(probe=False)

    assert snapshot["gpu_model"] == "NVIDIA RTX A6000"
    assert snapshot["capability_tier"] == "high"
    assert hardware.OVERRIDE_ENV in snapshot["overrides"] or "gpu_model" in snapshot["overrides"]

    monkeypatch.setenv(hardware.OVERRIDE_ENV, '{"vram_free_gb": 49, "vram_total_gb": 48}')
    with pytest.raises(hardware.HardwareConfigError):
        hardware.detect_hardware(probe=False)


def test_runtime_is_conservative_and_has_no_benchmark_eta():
    snapshot = hardware.detect_hardware(
        probe=False,
        override={
            "gpu_available": True,
            "cuda_available": True,
            "gpu_model": "NVIDIA RTX A6000",
            "vram_total_gb": 48.0,
            "vram_free_gb": 40.0,
            "cpu_logical_cores": 4,
            "ram_total_gb": 16.0,
            "ram_available_gb": 8.0,
        },
    )
    runtime = hardware.resolve_runtime(hardware=snapshot)

    assert runtime["capability_tier"] == "high"
    assert runtime["workers"]["sfm"] <= 8
    assert runtime["workers"]["io"] <= 4
    assert runtime["workers"]["torch"] <= 8
    assert runtime["cache"]["view_cache_budget_gb"] == pytest.approx(4.0)
    assert "relative_throughput" not in runtime
    assert "estimated_minutes" not in runtime


def test_demo_profile_is_exact_and_eta_is_model_bound():
    demo = profiles.resolve("demo", "workstation")
    assert (
        demo.source_long_edge,
        demo.crop,
        demo.cap_max,
        demo.iterations,
        demo.sh_degree,
        demo.colmap_long_edge,
    ) == (2304, 1536, 2_000_000, 15_000, 3, 3200)
    assert demo.estimated_minutes() is None
    assert demo.estimated_minutes(hardware={"gpu_model": "NVIDIA GeForce RTX 5090"}) == pytest.approx(4.3)
    assert demo.estimated_minutes(hardware={"gpu_model": "NVIDIA RTX A6000"}) is None
    assert demo.estimated_minutes(hardware={"hardware": {"gpu_model": "RTX 5090"}}) == pytest.approx(4.3)


def test_legacy_numbers_are_preserved_and_unsafe_coverage_is_explicit():
    assert (profiles.LAPTOP_DRAFT.source_long_edge, profiles.LAPTOP_DRAFT.crop, profiles.LAPTOP_DRAFT.cap_max) == (
        1600,
        512,
        400_000,
    )
    assert (profiles.WORKSTATION_ARCHIVE.source_long_edge, profiles.WORKSTATION_ARCHIVE.crop) == (4096, 1600)
    assert profiles.WORKSTATION_ARCHIVE.relative_throughput == 32.0
    warnings = profiles.WORKSTATION_ARCHIVE.validation_warnings()
    assert warnings and "below the 50%" in warnings[0]


def test_ram_guard_estimates_and_rejects_over_budget(tmp_path):
    model = _small_model()
    PILImage.new("RGB", (100, 50), (20, 30, 40)).save(tmp_path / "view.jpg")

    estimated, found = estimate_cache_bytes(model, tmp_path, long_edge=80)
    assert found == 1
    assert estimated == 80 * 40 * 3

    runtime = {"cache": {"view_cache_budget_gb": 0.000001, "safety_factor": 1.25}}
    report = ram_guard_report(model, tmp_path, long_edge=80, runtime=runtime)
    assert report["status"] == "exceeded"
    assert report["required_cache_gb"] > report["cache_budget_gb"]

    with pytest.raises(MemoryError, match="view cache preflight exceeds"):
        ViewSet(
            model,
            tmp_path,
            long_edge=80,
            device="cpu",
            undistort=False,
            runtime=runtime,
        )


def test_ram_guard_allows_small_cache_with_resolved_runtime(tmp_path):
    model = _small_model()
    PILImage.new("RGB", (100, 50), (20, 30, 40)).save(tmp_path / "view.jpg")
    runtime = {"cache": {"view_cache_budget_gb": 1.0, "safety_factor": 1.25}}

    views = ViewSet(
        model,
        tmp_path,
        long_edge=80,
        device="cpu",
        undistort=False,
        holdout_every=0,
        runtime=runtime,
    )
    assert len(views) == 1
    assert views.memory_footprint_gb() == pytest.approx(80 * 40 * 3 / 2**30)
