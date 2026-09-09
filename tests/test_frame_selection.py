from pathlib import Path

import numpy as np
import pytest

from vitrine import frame_selection
from vitrine.ingest import CameraGroup


def _group(count=20):
    return CameraGroup(
        name="video_clip",
        width=64,
        height=64,
        paths=[Path(f"frame_{index:05d}.png") for index in range(count)],
        from_video=True,
    )


def test_preset_validation_and_budget_cap():
    assert frame_selection.resolve_selection_preset("balanced").name == "balanced"
    with pytest.raises(ValueError, match="unknown selection_preset"):
        frame_selection.resolve_selection_preset("typo")
    with pytest.raises(ValueError, match="positive integer"):
        frame_selection.validate_selection_params("balanced", 0)
    with pytest.raises(ValueError, match="safety cap"):
        frame_selection.validate_selection_params("balanced", frame_selection.GLOBAL_FRAME_SAFETY_CAP + 1)


def test_adaptive_selection_scores_quality_and_keeps_temporal_coverage(monkeypatch):
    group = _group(20)

    def fake_gray(path):
        index = int(path.stem.split("_")[-1])
        # Deliberately repeat frames 4 and 5.  All other frames have enough
        # image distance to remain eligible for temporal bucket selection.
        value = 0.42 if index == 5 else 0.42 + ((index % 4) * 0.05)
        return np.full((64, 64), value, dtype=np.float32)

    monkeypatch.setattr(frame_selection, "_gray", fake_gray)
    result = frame_selection.select_adaptive(
        group,
        "balanced",
        budget=5,
        duration_seconds=20,
        sharpness_fn=lambda path: float(int(path.stem.split("_")[-1]) + 1),
    )

    assert len(result.kept) <= 5
    assert result.counters["scored"] == 20
    assert result.counters["duplicate_rejected"] >= 1
    assert result.kept[0].name.startswith("frame_")
    assert result.kept[-1].name.startswith("frame_")
    assert len(result.rejected) == result.counters["rejected"]

