from pathlib import Path

from PIL import Image

from vitrine import ingest as ingest_module
from vitrine.ingest import CameraGroup, _ingest_impl, classify_sources, extract_video_frames, select_sharpest


def _save(path: Path, value: int = 80, *, make: str | None = None, model: str | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (64, 48), (value, value, value))
    if make is not None:
        exif = image.getexif()
        exif[271] = make
        exif[272] = model or "camera"
        image.save(path, exif=exif.tobytes())
    else:
        image.save(path)


def test_classify_sources_separates_same_resolution_exif_cameras(tmp_path):
    source = tmp_path / "source" / "stills"
    _save(source / "a.jpg", make="Maker A", model="Body A")
    _save(source / "b.jpg", make="Maker B", model="Body B")

    groups = classify_sources(tmp_path / "source")

    assert len(groups) == 2
    assert {group.camera_model_hint for group in groups} == {"Maker A Body A", "Maker B Body B"}
    assert len({group.name for group in groups}) == 2


def test_classify_sources_accepts_a_single_image_file(tmp_path):
    source = tmp_path / "single.jpg"
    _save(source, value=96)

    groups = classify_sources(source)

    assert len(groups) == 1
    assert groups[0].paths == [source]


def test_explicit_legacy_sharpness_budget_is_stable(monkeypatch):
    paths = [Path(f"frame_{index:05d}.png") for index in range(8)]
    group = CameraGroup("video_clip", 64, 64, paths=paths, from_video=True)
    scores = {path: float(index) for index, path in enumerate(paths)}
    monkeypatch.setattr(ingest_module, "sharpness", lambda path: scores[path])

    first = select_sharpest(group, 3)
    second = select_sharpest(group, 3)

    assert first == second
    assert [path.name for path in first[0]] == ["frame_00002.png", "frame_00004.png", "frame_00007.png"]


def test_ingest_report_records_selection_counters_and_timings(tmp_path):
    source = tmp_path / "source"
    _save(source / "stills" / "one.jpg", value=96)
    out = tmp_path / "run" / "ingest"

    report = _ingest_impl(source, out, long_edge=64, stills_budget=1, video_budget=None)

    assert report.selection_preset == "balanced"
    assert report.video_budget is None
    assert report.counters["source_images"] == 1
    assert report.counters["staged_frames"] == 1
    assert report.timings["total_seconds"] >= 0
    assert (out / "ingest.json").is_file()


def test_extract_default_uses_full_duration_and_adaptive_safety_rate(monkeypatch, tmp_path):
    commands = []

    class FakeProcess:
        returncode = 0

        def __init__(self, command, **kwargs):
            commands.append(command)
            self.out_dir = Path(command[-1]).parent

        def __enter__(self):
            self.out_dir.mkdir(parents=True, exist_ok=True)
            for index in range(4):
                _save(self.out_dir / f"frame_{index + 1:05d}.png", value=80 + index)
            return self

        def __exit__(self, *_args):
            return False

        def poll(self):
            return 0

        def communicate(self, *args, **kwargs):
            return "", ""

    monkeypatch.setattr(
        ingest_module.subprocess,
        "run",
        lambda *args, **kwargs: type("Probe", (), {"returncode": 0, "stdout": "1000"})(),
    )
    monkeypatch.setattr(ingest_module.subprocess, "Popen", FakeProcess)

    group = extract_video_frames(tmp_path / "clip.mov", tmp_path / "frames")

    command = commands[0]
    assert "-frames:v" not in command
    assert group.duration_seconds == 1000
    assert group.safety_capped is True
    assert group.extracted_fps < ingest_module.VIDEO_EXTRACT_FPS
    assert len(group.paths) == 4
