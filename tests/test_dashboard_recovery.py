"""Dashboard recovery and truthful object-workflow state contracts.

These tests use only small JSON/text fixtures.  They exercise the local HTTP
control plane and do not claim anything about reconstruction quality or GPU
operation.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import vitrine.serve as serve
from vitrine.serve import UI_DIR, VitrineHandler, _objects_summary, _summarise_run, _worker_snapshot


_DEAD_PID = 2_147_483_647


def _pipeline(run: Path, *, state: str, stage_state: str = "running", pid: int | None = _DEAD_PID) -> None:
    run.joinpath("pipeline.json").write_text(
        json.dumps(
            {
                "schema": "vitrine/pipeline/1",
                "state": state,
                "pid": pid,
                "stages": {
                    "train": {
                        "state": stage_state,
                        "pid": pid,
                        "started": 1,
                        "heartbeat": 1,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def test_vanished_pipeline_worker_is_unknown_and_recoverable(tmp_path: Path) -> None:
    _pipeline(tmp_path, state="running")

    summary = _summarise_run(tmp_path)

    assert summary["headline"]["running"] is False
    assert summary["headline"]["interrupted"] is True
    assert summary["headline"]["worker_state"] == "unknown"
    assert summary["headline"]["worker_stale"] is True
    train = summary["stages"]["train"]
    assert train["running"] is False
    assert train["unknown"] is True
    assert train["done"] is False


def test_nonzero_child_exit_wins_over_persisted_complete_state(tmp_path: Path) -> None:
    _pipeline(tmp_path, state="complete", stage_state="complete", pid=None)

    class Exited:
        pid = 12345

        @staticmethod
        def poll() -> int:
            return 17

    worker = _worker_snapshot(tmp_path, Exited())

    assert worker["state"] == "failed"
    assert worker["running"] is False
    assert worker["returncode"] == 17
    assert worker["stale"] is True


def test_complete_stage_without_marker_is_not_reported_as_done(tmp_path: Path) -> None:
    _pipeline(tmp_path, state="complete", stage_state="complete", pid=None)

    summary = _summarise_run(tmp_path)

    train = summary["stages"]["train"]
    assert train["done"] is False
    assert train["invalid"] is True
    assert train["artifact_available"] is False
    assert summary["headline"]["interrupted"] is True
    assert summary["headline"]["worker_state"] == "failed"


def test_failed_stage_keeps_previous_artifact_available_but_not_current(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    (model / "scene.ply").write_bytes(b"previous-master")
    _pipeline(tmp_path, state="failed", stage_state="failed", pid=None)

    train = _summarise_run(tmp_path)["stages"]["train"]

    assert train["artifact_available"] is True
    assert train["done"] is False
    assert train["failed"] is True


def _objects_fixture(run: Path) -> None:
    objects = run / "objects" / "obj_001"
    objects.mkdir(parents=True)
    (objects / "asset.splat").write_bytes(b"0" * 32)
    (objects / "preview.png").write_bytes(b"png")
    (objects / "mask.png").write_bytes(b"mask")
    (run / "objects" / "objects.json").write_text(
        json.dumps(
            {
                "schema": "vitrine/object/1",
                "objects": [
                    {
                        "object_id": "obj_001",
                        "label": "Cabinet",
                        "splat_path": "obj_001/asset.splat",
                        "preview_path": "obj_001/preview.png",
                        "provenance": {
                            "frame_id": 14,
                            "camera_id": 3,
                            "instance_id": "cabinet-2",
                            "mask_sha256": "a" * 64,
                            "evidence": [
                                {
                                    "image_id": 14,
                                    "image_name": "frame-14.jpg",
                                    "camera_id": 3,
                                    "instance_id": "cabinet-2",
                                    "mask_path": "obj_001/mask.png",
                                    "mask_sha256": "b" * 64,
                                    "coordinate_space": "view",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    sfm = run / "sfm" / "sparse_text"
    sfm.mkdir(parents=True)
    (sfm / "images.txt").write_text("# registered\n", encoding="utf-8")


def _start_server(tmp_path: Path) -> ThreadingHTTPServer:
    runs = tmp_path / "runs"
    runs.mkdir(exist_ok=True)
    VitrineHandler.project_root = tmp_path
    VitrineHandler.runs_root = runs
    VitrineHandler.ui_dir = UI_DIR
    VitrineHandler.run_allowlist = None
    VitrineHandler.capture_processes = {}
    VitrineHandler.object_processes = {}
    VitrineHandler.mesh_processes = {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), VitrineHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _request(httpd: ThreadingHTTPServer, method: str, path: str, body: bytes = b"", content_type: str | None = None):
    conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
    headers = {"Content-Length": str(len(body))}
    if content_type:
        headers["Content-Type"] = content_type
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, json.loads(raw.decode("utf-8"))


def test_object_mesh_endpoint_forwards_only_selected_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "runs" / "capture"
    run.mkdir(parents=True)
    _objects_fixture(run)
    calls: list[tuple[list[str], dict]] = []

    class Running:
        pid = 4321

        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(serve.subprocess, "Popen", Running)
    httpd = _start_server(tmp_path)
    try:
        status, payload = _request(
            httpd,
            "POST",
            "/api/runs/capture/object-meshes",
            json.dumps({"object_id": "obj_001"}).encode(),
            "application/json",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert status == 202
    assert payload["object_id"] == "obj_001"
    assert calls and calls[0][0][-2:] == ["--object-id", "obj_001"]


def test_object_mesh_endpoint_rejects_unknown_selected_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = tmp_path / "runs" / "capture"
    run.mkdir(parents=True)
    _objects_fixture(run)
    called = False

    def unexpected(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("worker must not start for an unknown object")

    monkeypatch.setattr(serve.subprocess, "Popen", unexpected)
    httpd = _start_server(tmp_path)
    try:
        status, payload = _request(
            httpd,
            "POST",
            "/api/runs/capture/object-meshes",
            json.dumps({"object_id": "obj_999"}).encode(),
            "application/json",
        )
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert status == 409
    assert "not present" in payload["error"]
    assert called is False


def test_live_construction_clean_exit_without_terminal_record_is_unknown(tmp_path: Path) -> None:
    run = tmp_path / "runs" / "capture"
    run.mkdir(parents=True)
    _pipeline(run, state="running")

    class Exited:
        pid = 4322

        @staticmethod
        def poll():
            return 0

    httpd = _start_server(tmp_path)
    VitrineHandler.capture_processes["capture"] = Exited()
    try:
        status, payload = _request(httpd, "GET", "/api/runs/capture/construction")
    finally:
        httpd.shutdown()
        httpd.server_close()

    assert status == 200
    assert payload["state"] == payload["worker_state"] == "unknown"
    assert payload["worker_stale"] is True
    assert payload["returncode"] == 0
    assert "terminal state" in payload["error"]
    assert payload["can_resume"] is True
    assert payload["done"]["train"] is False
    assert payload["stage_states"]["train"]["unknown"] is True


def test_object_summary_exposes_copied_evidence_identity_without_external_paths(tmp_path: Path) -> None:
    _objects_fixture(tmp_path)

    item = _objects_summary(tmp_path)["objects"][0]

    assert item["evidence"]["has_source"] is True
    assert item["evidence"]["identity"] == {
        "frame_id": 14,
        "camera_id": 3,
        "instance_id": "cabinet-2",
        "mask_sha256": "a" * 64,
    }
    assert item["evidence"]["has_mask"] is True
    assert item["evidence"]["observations"][0]["image_id"] == 14
    assert item["evidence"]["observations"][0]["mask_path"] == "objects/obj_001/mask.png"
    assert item["evidence"]["items"]
    assert all(entry["url"].startswith("/files/") for entry in item["evidence"]["items"])


def test_static_pages_reference_local_assets_for_offline_rehearsal() -> None:
    attr = re.compile(r"(?:src|href)=[\"']([^\"']+)[\"']")
    for name in ("index.html", "viewer.html", "mesh-viewer.html", "construction.html"):
        text = (UI_DIR / name).read_text(encoding="utf-8")
        urls = [url for url in attr.findall(text) if not url.startswith(("#", "mailto:"))]
        assert all(not url.startswith(("http://", "https://", "//")) for url in urls), name


def test_ui_cli_forwards_external_runs_root_without_moving_project_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vitrine import cli

    external = tmp_path / "capture data" / "runs"
    seen: dict = {}

    def fake_serve(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("vitrine.serve.serve", fake_serve)
    args = cli.build_parser().parse_args(["ui", "--runs-root", str(external)])

    assert cli.cmd_ui(args) == 0
    assert seen["runs_root"] == external


def test_serve_binds_external_runs_root_and_keeps_ui_project_root(tmp_path: Path) -> None:
    from vitrine.serve import serve

    project = tmp_path / "project"
    external = tmp_path / "capture data" / "runs"
    ready: queue.Queue = queue.Queue()

    def on_ready(url, server):
        ready.put((url, server))
        threading.Thread(target=server.shutdown, daemon=True).start()

    thread = threading.Thread(
        target=serve,
        kwargs={
            "project_root": project,
            "runs_root": external,
            "port": 0,
            "on_ready": on_ready,
        },
        daemon=True,
    )
    thread.start()
    url, server = ready.get(timeout=10)
    thread.join(timeout=10)

    assert url.startswith("http://127.0.0.1:")
    assert external.is_dir()
    assert VitrineHandler.runs_root == external.resolve()
    assert VitrineHandler.ui_dir == UI_DIR
    assert not thread.is_alive()
