"""Dashboard capture upload: Vitrine App session import is locked."""

from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from vitrine.serve import VitrineHandler, UI_DIR


def _start(tmp_path: Path) -> ThreadingHTTPServer:
    (tmp_path / "runs").mkdir()
    VitrineHandler.project_root = tmp_path
    VitrineHandler.runs_root = tmp_path / "runs"
    VitrineHandler.ui_dir = UI_DIR
    VitrineHandler.run_allowlist = None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), VitrineHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_session_zip_upload_is_coming_soon(tmp_path):
    httpd = _start(tmp_path)
    try:
        boundary = "----vitrine"
        body = (
            b"------vitrine\r\n"
            b'Content-Disposition: form-data; name="title"\r\n\r\n'
            b"Nested Cinema\r\n"
            b"------vitrine\r\n"
            b'Content-Disposition: form-data; name="session"; filename="session.zip"\r\n'
            b"Content-Type: application/zip\r\n\r\n"
            b"PK\x03\x04fake\r\n"
            b"------vitrine--\r\n"
        )
        conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request(
            "POST",
            "/api/captures",
            body=body,
            headers={
                "Content-Type": f'multipart/form-data; boundary="{boundary}"',
                "Content-Length": str(len(body)),
            },
        )
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 501
        assert "coming soon" in payload["error"].lower()
        assert not any((tmp_path / "runs").iterdir())
    finally:
        httpd.shutdown()
        httpd.server_close()
