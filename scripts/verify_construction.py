"""Validate preview publication against a real capture, never synthetic geometry.

Usage: .venv/Scripts/python.exe scripts/verify_construction.py --run-dir runs/NAME
Writes only to a unique output/construction-verification-* directory.
This is not a substitute for the paired full-training benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    source = args.run_dir.resolve()
    required = [source / "sfm/sparse_text/cameras.txt", source / "sfm/sparse_text/images.txt",
                source / "sfm/sparse_text/points3D.txt", source / "model/scene.ply"]
    if not all(p.is_file() for p in required):
        parser.error("A real capture with a text COLMAP model and trained scene.ply is required")

    import numpy as np
    import torch
    from vitrine.colmap_io import read_model, scene_scale
    from vitrine.construction import SnapshotStore, TrainingPreview, atomic_json, read_json, sparse_payload
    from vitrine.ply import read_splat_ply
    from vitrine.train import MAX_SCALE_FRACTION

    def digest(path):
        with path.open("rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()

    originals = {p: digest(p) for p in required}
    model = read_model(source / "sfm/sparse_text")
    geometry = sparse_payload(model)
    assert len(geometry["cameras"]) == len(model.images)
    assert {c["camera_id"] for c in geometry["cameras"]} == {v.camera_id for v in model.images}
    output = Path(__file__).resolve().parents[1] / "output" / ("construction-verification-" + uuid.uuid4().hex)
    output.mkdir(parents=True)
    store = SnapshotStore(output / "model")
    # The actual camera payload exercises publication, failure and retention.
    first = store.publish("sparse", ".json", lambda p: atomic_json(p, geometry))
    manifest_before = store.manifest.read_bytes()
    def failed_writer(path):
        path.write_bytes(b"partial")
        raise OSError("Deliberately interrupted preview export")
    try:
        store.publish("sparse", ".json", failed_writer)
        raise AssertionError("failed writer unexpectedly succeeded")
    except OSError:
        pass
    assert store.manifest.read_bytes() == manifest_before
    assert not list(store.folder.glob("*.tmp"))
    for _ in range(121):
        last = store.publish("sparse", ".json", lambda p: atomic_json(p, geometry))
    manifest = read_json(store.manifest)["snapshots"]
    assert len(manifest) <= 120 and manifest[0]["id"] == first["id"] and manifest[-1]["id"] == last["id"]
    assert len(list(store.folder.glob("preview-*"))) == len(manifest)

    data = read_splat_ply(source / "model/scene.ply")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    params = {k: torch.nn.Parameter(torch.from_numpy(np.asarray(data[k]).copy()).to(device))
              for k in ("means", "scales", "quats", "opacities", "sh0")}
    def tensor_hash(value):
        return hashlib.sha256(value.detach().cpu().numpy().tobytes()).hexdigest()
    before = {k: tensor_hash(v) for k,v in params.items()}
    cpu_rng = torch.get_rng_state().clone()
    gpu_rng = torch.cuda.get_rng_state().clone() if device == "cuda" else None
    preview = TrainingPreview(output / "model", scene_scale(model), MAX_SCALE_FRACTION)
    started = time.perf_counter()
    preview.capture(params, 0, force=True)
    preview.close()
    elapsed = time.perf_counter() - started
    assert all(tensor_hash(v) == before[k] for k,v in params.items())
    assert torch.equal(cpu_rng, torch.get_rng_state())
    if gpu_rng is not None:
        assert torch.equal(gpu_rng, torch.cuda.get_rng_state())
    manifest = read_json(store.manifest)["snapshots"]
    shot = manifest[-1]
    assert shot["kind"] == "splat", read_json(output / "model/construction-preview-error.json")
    assert (store.folder / shot["id"]).stat().st_size % 32 == 0
    assert shot["sampled_gaussians"] <= 250_000
    assert all(digest(p) == checksum for p,checksum in originals.items())
    report = {"source": str(source), "device": device, "export_seconds": elapsed,
              "publication_retention_and_failure": "passed", "parameters_and_rng": "unchanged",
              "originals": "unchanged", "paired_training_benchmark": "not performed"}
    atomic_json(output / "verification.json", report)
    print(json.dumps(report, indent=2))
    print("Report:", output / "verification.json")


if __name__ == "__main__":
    main()
