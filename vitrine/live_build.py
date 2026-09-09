"""Read-only construction monitor for normal runs and isolated experiments."""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def activity(runs_root: Path):
    jobs = []
    for run in sorted(runs_root.iterdir()) if runs_root.is_dir() else []:
        if not run.is_dir() or run.is_symlink() or run.name.startswith("."):
            continue
        candidates = [run / "model"]
        experiments = run / "experiments"
        if experiments.is_dir() and not experiments.is_symlink():
            candidates += sorted(p for p in experiments.iterdir() if p.is_dir())
        for folder in candidates:
            if folder.is_symlink():
                continue
            training = folder / "model" if (folder / "model").is_dir() and not (folder / "model").is_symlink() else folder
            progress = read_json(training / "progress.json")
            complete = read_json(training / "train.json")
            snapshots = []
            for path in sorted((training / "construction").glob("step-*.json")):
                metadata = read_json(path)
                picture = path.with_suffix(".jpg")
                if metadata and picture.is_file() and not picture.is_symlink():
                    relative = picture.relative_to(runs_root).as_posix()
                    snapshots.append({**metadata, "url": "/api/construction-image/" + quote(relative, safe="/")})
            # Historical completed captures without a construction sequence stay in Library.
            from .construction import construction_payload
            construction = construction_payload(run, folder=None if folder.name == "model" else folder)
            if not progress and not snapshots and not construction["snapshots"] and not construction.get("heartbeat") and (not complete or folder.name == "model"):
                continue
            data = complete or progress or {}
            stamp_path = training / ("train.json" if complete else "progress.json")
            try:
                stamp = stamp_path.stat().st_mtime
            except OSError:
                stamp = 0
            stamp = max(stamp, construction.get("heartbeat", 0))
            if time.time()-stamp > 86400 and not snapshots and not construction["snapshots"]:
                continue
            jobs.append({"id": folder.relative_to(runs_root).as_posix(), "run": run.name,
                         "label": folder.name if folder.name != "model" else "Reconstruction",
                         "state": "complete" if complete else "running" if time.time()-stamp < 180 else "stale",
                         "updated": stamp, "progress": data, "snapshots": snapshots})
    return {"jobs": sorted(jobs, key=lambda j:j["updated"], reverse=True)}


def construction_image(runs_root: Path, relative: str):
    root = runs_root.resolve()
    path = root / unquote(relative)
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return None
    if path.parent.name != "construction" or not path.name.startswith("step-") or path.suffix != ".jpg":
        return None
    return path if path.is_file() and not path.is_symlink() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--runs-root", type=Path, default=Path(__file__).resolve().parents[1]/"runs")
    args = parser.parse_args()
    assets = Path(__file__).parent / "ui"
    class Monitor(SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):
            super().__init__(*a,directory=str(assets),**kw)
        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:8765")
            super().end_headers()
        def do_GET(self):
            path=urlparse(self.path).path
            if path == "/api/construction":
                payload=json.dumps(activity(args.runs_root)).encode()
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(payload)))
                self.end_headers();self.wfile.write(payload);return
            if path.startswith("/api/runs/") and path.endswith("/construction"):
                from .serve import _safe_run_dir
                from .construction import construction_payload
                from urllib.parse import parse_qs
                name = unquote(path[len("/api/runs/"):-len("/construction")])
                run = _safe_run_dir(args.runs_root, name)
                if run is None:return self.send_error(404)
                experiment = (parse_qs(urlparse(self.path).query).get("experiment") or [None])[0]
                folder = run / "experiments" / experiment if experiment else None
                if folder and (not folder.is_dir() or folder.is_symlink() or not folder.resolve().is_relative_to(run.resolve()) or folder.resolve().parent != (run / "experiments").resolve()):return self.send_error(404)
                payload=json.dumps(construction_payload(run, folder=folder)).encode()
                self.send_response(200);self.send_header("Content-Type","application/json")
                self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload);return
            if path.startswith("/files/"):
                from .serve import _safe_run_dir, _safe_file_under_run
                import mimetypes
                parts = unquote(path[len("/files/"):]).split("/", 1)
                run = _safe_run_dir(args.runs_root, parts[0])
                file = _safe_file_under_run(run, parts[1]) if run and len(parts)==2 else None
                if file is None:return self.send_error(404)
                payload=file.read_bytes();self.send_response(200)
                self.send_header("Content-Type",mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload);return
            if path.startswith("/api/construction-image/"):
                image=construction_image(args.runs_root,path[len("/api/construction-image/"):])
                if image is None:return self.send_error(404)
                payload=image.read_bytes()
                self.send_response(200);self.send_header("Content-Type","image/jpeg")
                self.send_header("Content-Length",str(len(payload)));self.end_headers();self.wfile.write(payload);return
            if path == "/":self.path="/construction.html"
            elif path.startswith("/static/"):self.path=path[len("/static"):]
            super().do_GET()
    ThreadingHTTPServer(("127.0.0.1",args.port),Monitor).serve_forever()


if __name__ == "__main__":main()
