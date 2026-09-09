"""Stdlib multipart parser used by the dashboard upload POST handler."""

from __future__ import annotations

import io

from vitrine.serve import _parse_multipart_form


class _Headers(dict):
    def get(self, key, default=""):  # noqa: ANN001
        return super().get(key, default)


def _form(body: bytes, boundary: str = "----vitrine") -> object:
    headers = _Headers({
        "Content-Type": f'multipart/form-data; boundary="{boundary}"',
        "Content-Length": str(len(body)),
    })
    return _parse_multipart_form(io.BytesIO(body), headers)


def test_text_fields_and_file_upload():
    jpeg = b"\xff\xd8\xffpayload"
    body = (
        b"------vitrine\r\n"
        b'Content-Disposition: form-data; name="title"\r\n\r\n'
        b"Nested Cinema\r\n"
        b"------vitrine\r\n"
        b'Content-Disposition: form-data; name="files"; filename="wall.jpg"\r\n'
        b"Content-Type: image/jpeg\r\n\r\n"
        + jpeg + b"\r\n"
        b"------vitrine--\r\n"
    )
    form = _form(body)
    assert form.getfirst("title", "New capture") == "Nested Cinema"
    assert form.getfirst("quality", "standard") == "standard"
    items = form["files"]
    if not isinstance(items, list):
        items = [items]
    assert items[0].filename == "wall.jpg"
    assert items[0].file.read() == jpeg


def test_import_serve_without_cgi():
    import vitrine.serve as serve

    assert not hasattr(serve, "cgi")
    assert callable(serve._parse_multipart_form)
