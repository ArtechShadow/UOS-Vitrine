"""Opt-in integration check: VITRINE_TEST_ARW must name a real Sony photograph."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image

from vitrine.ingest import _exif_camera_label, ingest, sharpness
from vitrine.raw_io import raw_info
from vitrine.serve import UPLOAD_SUFFIXES


def test_real_sony_arw_ingest(tmp_path):
    configured = os.environ.get("VITRINE_TEST_ARW")
    if not configured:
        pytest.skip("Set VITRINE_TEST_ARW to a real Sony ARW photograph")
    source = Path(configured)
    assert source.is_file() and source.suffix.lower() == ".arw"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    (width, height), original_exif = raw_info(source)
    if original_exif.get(274, 1) in (5, 6, 7, 8):
        width, height = height, width
    assert ".arw" in UPLOAD_SUFFIXES
    assert sharpness(source) > 0
    report = ingest(source, tmp_path / "ingest", long_edge=1600)
    assert report.accepted == 1
    assert (report.groups[0]["width"], report.groups[0]["height"]) == (width, height)
    assert report.groups[0]["colour_processing"]["raw_development"]["auto_brightness"] is False
    output, = (tmp_path / "ingest/images").rglob("*.jpg")
    with Image.open(output) as image:
        assert image.mode == "RGB" and max(image.size) == min(1600, max(width, height))
        assert abs(image.width / image.height - width / height) < .005
        assert image.getexif()[274] == 1
        assert image.getexif()[272] == original_exif[272]
        assert image.info["icc_profile"]
        assert image.getexif().get_ifd(34665)[37386] == original_exif.get_ifd(34665)[37386]
    assert _exif_camera_label(output) == _exif_camera_label(source)
    preview = json.loads((tmp_path / "ingest/selection.json").read_text())
    assert preview["complete"] and preview["kept"] == 1
    assert (tmp_path / "ingest/selection-thumbnails" / preview["records"][0]["thumbnail"]).is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
