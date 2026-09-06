"""Opt-in checks against a running dashboard and its real master capture.

Run with VITRINE_DEMO_URL=http://127.0.0.1:8765 and pytest on this file.
No generated captures or object assets are used. The POST check only runs
when the service explicitly reports that no sidecar is configured.
"""

import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


BASE = os.environ.get("VITRINE_DEMO_URL", "").rstrip("/")
RUN = os.environ.get("VITRINE_DEMO_RUN", "nested-cinema-04-master")
pytestmark = pytest.mark.skipif(not BASE, reason="Set VITRINE_DEMO_URL for real capture checks")


def workflow():
    with urlopen(f"{BASE}/api/runs/{RUN}/objects", timeout=20) as response:
        return json.load(response)


def test_live_real_splat_inputs_are_served():
    state = workflow()
    assert state["ready"] is True
    inputs = {item["name"]: item for item in state["inputs"]}
    assert {"scene.ply", "scene.splat"} <= inputs.keys()
    for name in ("scene.ply", "scene.splat"):
        item = inputs[name]
        assert item["bytes"] > 1024
        request = Request(BASE + item["url"], headers={"Range": "bytes=0-31"})
        with urlopen(request, timeout=20) as response:
            assert response.status in (200, 206)
            header = response.read(32)
            assert len(header) == 32
            if name.endswith(".ply"):
                assert header.startswith(b"ply")


def test_live_missing_sidecar_is_explicit_and_cannot_launch():
    state = workflow()
    if state["configured"]:
        pytest.skip("Sidecar configured; refusing to launch GPU work from a smoke test")
    assert state["running"] is False
    request = Request(f"{BASE}/api/runs/{RUN}/objects", data=b"{}",
                      headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=20)
    assert caught.value.code == 409
    assert "not configured" in json.load(caught.value)["error"].lower()
    assert workflow()["running"] is False


def test_live_real_output_manifest_if_present():
    outputs = workflow()["outputs"]
    if outputs is None:
        pytest.skip("No real object separation output exists on this capture")
    assert outputs["count"] == len(outputs["objects"])
    assert outputs["count"] > 0
    for item in outputs["objects"]:
        url = item.get("splat_url") or item.get("mesh_url")
        assert url, f"Missing real asset for {item['object_id']}"
        with urlopen(BASE + url, timeout=20) as response:
            assert response.read(16)
