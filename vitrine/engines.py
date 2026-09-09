"""Lazy reconstruction providers. External mesh models never enter core imports."""
from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import uuid
from pathlib import Path
from typing import Protocol

from .construction import atomic_json
from .telemetry import measure


class ReconstructionEngine(Protocol):
    def train(self, model, images_dir, output_dir, profile, **kwargs): ...
    def export(self, source: Path, output: Path) -> Path: ...
    def validate(self, source: Path) -> dict: ...
    def cleanup(self, source: Path, output: Path, **kwargs): ...


class GsplatEngine:
    name = "gsplat"

    def train(self, model, images_dir, output_dir, profile, **kwargs):
        from .train import train
        return train(model, images_dir, output_dir, profile, **kwargs)

    def validate(self, source):
        import numpy as np
        from .ply import read_splat_ply
        data = read_splat_ply(Path(source))
        count = len(data["means"])
        if not count or any(not np.isfinite(data[key]).all() for key in
                            ("means", "scales", "quats", "opacities", "sh0", "shN")):
            raise ValueError("The Gaussian master is empty or contains non-finite values")
        return {"gaussians": count, "sh_degree": data["sh_degree"]}

    def export(self, source, output):
        from .export import write_splat_file
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(output.name + ".tmp-" + uuid.uuid4().hex)
        with measure(output.parent, "viewer_export"):
            write_splat_file(Path(source), temporary)
            if temporary.stat().st_size == 0 or temporary.stat().st_size % 32:
                raise ValueError("Viewer export did not produce valid splat records")
            os.replace(temporary, output)
        return output

    def cleanup(self, source, output, **kwargs):
        from .cleanup import cleanup_splat
        return cleanup_splat(source, output, **kwargs)


def get_engine(name="gsplat") -> ReconstructionEngine:
    if name != "gsplat":
        raise ValueError(f"Unknown reconstruction engine {name!r}; installed baseline: gsplat")
    return GsplatEngine()


def validate_glb(path: Path):
    """Validate container structure before publishing provider output."""
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) != 12:
            raise ValueError("GLB header is incomplete")
        magic, version, length = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or length != path.stat().st_size:
            raise ValueError("Provider output is not a complete GLB 2.0 file")
        chunk = handle.read(8)
        if len(chunk) != 8:
            raise ValueError("Missing GLB JSON chunk")
        size, kind = struct.unpack("<I4s", chunk)
        if kind != b"JSON" or size > min(length-20, 16*2**20):
            raise ValueError("Invalid GLB JSON chunk")
        document = json.loads(handle.read(size))
        if not document.get("meshes"):
            raise ValueError("GLB contains no mesh")
        if any("uri" in buffer for buffer in document.get("buffers", [])):
            raise ValueError("GLB must embed its mesh buffers for offline use")
    return document


class LocalMeshEngine:
    """File handoff: isolated image/mask manifest → embedded GLB in a separate process.

    A local adapter supplies the heavy environment (e.g. TRELLIS or Hunyuan).
    Merely configuring an executable does not imply installed weights/readiness.
    """

    def __init__(self, command: list[str], weights: list[str] | None = None):
        if not command or not all(isinstance(v, str) and v for v in command):
            raise ValueError("Mesh provider command must be a nonempty JSON string array")
        if not (Path(command[0]).is_file() or shutil.which(command[0])):
            raise ValueError("Mesh provider executable is missing; install/configure the optional local adapter")
        for weight in weights or []:
            if not Path(weight).is_file() or Path(weight).stat().st_size == 0:
                raise ValueError(f"Mesh model weight is missing or empty: {weight}")
        self.command = command

    def reconstruct(self, manifest: Path, destination: Path, timeout=1800):
        from .objects import resolve_contained, sha256_file
        from PIL import Image
        manifest, destination = Path(manifest).resolve(), Path(destination).resolve()
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        if doc.get("schema") != "vitrine/isolated-images/1" or not doc.get("images"):
            raise ValueError("Expected vitrine/isolated-images/1 with image/mask pairs")
        for entry in doc["images"]:
            image = resolve_contained(entry["image"], "image", manifest.parent)
            mask = resolve_contained(entry["mask"], "mask", manifest.parent)
            with Image.open(image) as picture, Image.open(mask) as matte:
                picture.load(); matte.load()
                if picture.size != matte.size or matte.convert("L").getbbox() is None:
                    raise ValueError("Each mask must be nonempty and match its image dimensions")
        if destination.exists():
            raise ValueError("Choose a new mesh output directory; existing generations are preserved")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name(destination.name + ".working-" + uuid.uuid4().hex)
        staging.mkdir()
        with measure(staging, "mesh_reconstruction", input_frames=len(doc["images"])):
            output = staging / "mesh.glb"
            with (staging / "provider.log").open("wb") as log:
                result = subprocess.run([*self.command, "--input", str(manifest), "--output", str(output)],
                                        stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError(f"Local mesh provider exited {result.returncode}; see {staging / 'provider.log'}")
            validate_glb(output)
            atomic_json(staging / "manifest.json", dict(schema="vitrine/local-mesh/1", file="mesh.glb",
                        sha256=sha256_file(output), input_sha256=sha256_file(manifest), engine="external-local"))
        os.replace(staging, destination)
        return destination / "mesh.glb"
