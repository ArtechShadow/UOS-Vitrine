"""Inspect a real GLB for embedded, UV-mapped metallic-roughness PBR assets."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import struct
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.engines import validate_glb


def inspect(path):
    path = Path(path)
    doc = validate_glb(path)
    with path.open('rb') as stream:
        stream.seek(12)
        length, _ = struct.unpack('<I4s', stream.read(8))
        stream.seek(length, 1)
        length, kind = struct.unpack('<I4s', stream.read(8))
        if kind != b'BIN\0':
            raise ValueError('No embedded binary chunk')
        binary = stream.read(length)
        if len(binary) != length:
            raise ValueError('Truncated embedded binary chunk')
    images = []
    for record in doc.get('images', []):
        if 'uri' in record or 'bufferView' not in record:
            raise ValueError('Texture is not embedded')
        view = doc['bufferViews'][record['bufferView']]
        offset = view.get('byteOffset', 0)
        data = binary[offset:offset + view['byteLength']]
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            array = np.asarray(image.convert('RGB'))
            images.append(dict(size=list(image.size), bytes=len(data),
                               mean_rgb=array.mean(axis=(0, 1)).round(3).tolist(),
                               std_rgb=array.std(axis=(0, 1)).round(3).tolist(),
                               sha256=hashlib.sha256(data).hexdigest()))
    if not images:
        raise ValueError('No embedded texture images')
    materials = []
    for material in doc.get('materials', []):
        pbr = material.get('pbrMetallicRoughness', {})
        required = dict(basecolour=pbr.get('baseColorTexture'),
                        metallic_roughness=pbr.get('metallicRoughnessTexture'),
                        normal=material.get('normalTexture'), occlusion=material.get('occlusionTexture'))
        if not all(required.values()):
            raise ValueError('Material is missing a required PBR texture: ' + str(required))
        for name, texture in required.items():
            if texture.get('texCoord', 0) != 0:
                raise ValueError('Unexpected texture coordinate set')
            image_index = doc['textures'][texture['index']]['source']
            if image_index >= len(images):
                raise ValueError('Invalid image index')
        materials.append(dict(name=material.get('name'), maps=required))
    primitives = []
    for mesh in doc['meshes']:
        for primitive in mesh['primitives']:
            attributes = primitive['attributes']
            if not {'POSITION', 'NORMAL', 'TEXCOORD_0'} <= attributes.keys():
                raise ValueError('Mesh is missing positions, normals or UV coordinates')
            if 'material' not in primitive or primitive['material'] >= len(materials):
                raise ValueError('Mesh has no validated PBR material')
            for name in ('POSITION', 'NORMAL', 'TEXCOORD_0'):
                accessor = doc['accessors'][attributes[name]]
                view = doc['bufferViews'][accessor['bufferView']]
                columns = 2 if name == 'TEXCOORD_0' else 3
                if accessor['componentType'] != 5126:
                    raise ValueError('Expected floating-point geometry')
                offset = view.get('byteOffset', 0) + accessor.get('byteOffset', 0)
                stride = view.get('byteStride', columns * 4)
                array = np.ndarray((accessor['count'], columns), dtype='<f4',
                                   buffer=binary, offset=offset, strides=(stride, 4))
                if not np.isfinite(array).all():
                    raise ValueError('Non-finite geometry or UV coordinates')
            primitives.append(dict(vertices=doc['accessors'][attributes['POSITION']]['count'],
                                   indices=doc['accessors'][primitive['indices']]['count']))
    return dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                bytes=path.stat().st_size,materials=materials,textures=images,primitives=primitives,
                structural_pbr_complete=True,visual_quality='requires separate visual assessment')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('glb', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = inspect(args.glb)
    rendered = json.dumps(result, indent=2)
    if args.report:
        args.report.write_text(rendered, encoding='utf-8')
    print(rendered)
