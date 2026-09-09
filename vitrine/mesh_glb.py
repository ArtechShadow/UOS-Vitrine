"""Export an existing triangle PLY as a self-contained, vertex-coloured GLB."""
from __future__ import annotations

import json
import os
import struct
import uuid
from pathlib import Path


def write_mesh_glb(source: Path, target: Path) -> Path:
    import numpy as np
    from plyfile import PlyData
    from .engines import validate_glb
    data = PlyData.read(source)
    vertex = data["vertex"]
    positions = np.column_stack([vertex[key] for key in ("x", "y", "z")]).astype("<f4")
    faces = list(data["face"]["vertex_indices"])
    if not len(positions) or not faces or not np.isfinite(positions).all():
        raise ValueError("Mesh requires finite vertices and triangles")
    if any(len(face) != 3 for face in faces):
        raise ValueError("Only triangle meshes can be exported to GLB")
    indices = np.asarray(faces, dtype=np.int64)
    if indices.min() < 0 or indices.max() >= len(positions):
        raise ValueError("Mesh contains invalid triangle indices")
    chunks, views, accessors = [], [], []

    def append(array, component, shape, target_type, bounds=False):
        offset = sum(len(chunk) for chunk in chunks)
        payload = array.tobytes()
        views.append(dict(buffer=0, byteOffset=offset, byteLength=len(payload), target=target_type))
        chunks.append(payload + b"\0" * (-len(payload) % 4))
        accessor = dict(bufferView=len(views)-1, componentType=component, count=len(array), type=shape)
        if bounds:
            accessor.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        accessors.append(accessor)
        return len(accessors)-1

    attrs = {"POSITION": append(positions, 5126, "VEC3", 34962, True)}
    fields = set(vertex.data.dtype.names)
    if {"red", "green", "blue"} <= fields:
        color = np.column_stack([vertex[key] for key in ("red", "green", "blue")]).astype(np.float32) / 255
        # PLY vertex colours are sRGB; glTF vertex colours are linear.
        color = np.where(color <= .04045, color / 12.92, ((color+.055)/1.055)**2.4).astype("<f4")
        attrs["COLOR_0"] = append(color, 5126, "VEC3", 34962)
    index = append(indices.astype("<u4").reshape(-1), 5125, "SCALAR", 34963)
    binary = b"".join(chunks)
    document = dict(asset=dict(version="2.0", generator="Vitrine triangle mesh export"),
                    scene=0, scenes=[dict(nodes=[0])], nodes=[dict(mesh=0)],
                    meshes=[dict(primitives=[dict(attributes=attrs, indices=index, mode=4, material=0)])],
                    materials=[dict(doubleSided=True, pbrMetallicRoughness=dict(metallicFactor=0, roughnessFactor=1))],
                    buffers=[dict(byteLength=len(binary))], bufferViews=views, accessors=accessors)
    text = json.dumps(document, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    with temporary.open("wb") as handle:
        handle.write(struct.pack("<4sII", b"glTF", 2, 28+len(text)+len(binary)))
        handle.write(struct.pack("<I4s", len(text), b"JSON")); handle.write(text)
        handle.write(struct.pack("<I4s", len(binary), b"BIN\0")); handle.write(binary)
    validate_glb(temporary)
    os.replace(temporary, target)
    return target
