"""Experimental surface derivatives from validated object splats.

Outputs are independent of objects.json: its original asset/hash contract is
unchanged. Only complete, validated generations enter published/.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import uuid
import re
import time
from urllib.parse import quote
import shutil

import numpy as np

from .construction import Progress, atomic_json
from .objects import load_validated_objects, resolve_contained, sha256_file


def archive_meshes(source: Path, destination: Path):
    """Copy only the latest complete generation, checking every mesh digest."""
    from .construction import read_json
    manifest = read_json(source / 'latest.json')
    if manifest is None:
        return 0
    generation = manifest.get('generation')
    if manifest.get('schema') != 'vitrine/object-mesh/1' or not re.fullmatch('[0-9a-f]{32}', str(generation)):
        raise ValueError('Invalid object mesh generation')
    folder = source / 'published' / generation
    if read_json(folder / 'manifest.json') != manifest:
        raise ValueError('Mesh generation manifest mismatch')
    files = []
    for rec in manifest['objects']:
        file = resolve_contained(rec['file'], 'mesh file', folder.resolve())
        if not re.fullmatch(r'object-[0-9]{4,}\.ply', str(rec['file'])) or (folder / rec['file']).is_symlink() or file.suffix != '.ply' or file.is_symlink() or sha256_file(file) != rec['sha256']:
            raise ValueError('Mesh derivative checksum mismatch')
        files.append(file)
    # Refuse links anywhere in the publication path, including ancestors.
    if any(p.is_symlink() for p in (source, source / 'published', folder)):
        raise ValueError('Refusing symlinked mesh generation')
    destination.mkdir(parents=True, exist_ok=True)
    for file in files:
        shutil.copy2(file, destination / file.name)
    atomic_json(destination / 'manifest.json', manifest)
    return len(files)+1


def mesh_summary(run_dir, process=None):
    from .construction import read_json
    root = Path(run_dir) / 'object-meshes'
    status = read_json(root / 'construction-status.json') or {}
    if process is not None:
        code = process.poll()
        status = dict(status, state='running' if code is None else 'failed' if code else 'done')
    elif status.get('state') == 'running' and time.time()-status.get('heartbeat', 0) > 30:
        status = dict(status, state='unknown', message='Mesh worker connection lost; status unknown')
    manifest = read_json(root / 'latest.json') or {}
    generation = manifest.get('generation', '')
    items = []
    if re.fullmatch('[0-9a-f]{32}', str(generation)):
        source_doc = read_json(Path(run_dir) / 'objects' / 'objects.json') or {}
        hashes = {r.get('object_id'): r.get('sha256') for r in source_doc.get('objects', []) if isinstance(r, dict)}
        for rec in manifest.get('objects', []):
            if not isinstance(rec, dict) or not re.fullmatch(r'object-[0-9]{4,}\.ply', str(rec.get('file', ''))):
                continue
            rel = f"object-meshes/published/{generation}/{rec['file']}"
            path = Path(run_dir) / rel
            if path.is_file() and path.resolve().is_relative_to(Path(run_dir).resolve()):
                items.append(dict(rec, stale=hashes.get(rec.get('object_id')) != rec.get('source_sha256'),
                                  url=f'/files/{quote(Path(run_dir).name)}/{rel}'))
    return dict(status=status, objects=items)


def splat_to_ply(source: Path, target: Path):
    from .ply import write_splat_ply, rgb_to_sh_dc

    raw = np.fromfile(source, dtype=np.uint8)
    if not raw.size or raw.size % 32:
        raise ValueError("Invalid binary .splat length")
    rows = raw.reshape(-1, 32)
    geometry = rows[:, :24].copy().view('<f4').reshape(-1, 6)
    means, scales = geometry[:, :3], geometry[:, 3:]
    quats = (rows[:, 28:32].astype(np.float32) - 128) / 128
    if (not np.isfinite(geometry).all() or (scales <= 0).any()
            or (np.linalg.norm(quats, axis=1) == 0).any()):
        raise ValueError("Splat contains invalid positions, scales or rotations")
    opacity = np.clip(rows[:, 27].astype(np.float32) / 255, 1e-6, 1-1e-6)
    write_splat_ply(target, means=means, scales=np.log(scales), quats=quats,
                    opacities=np.log(opacity / (1-opacity)),
                    sh0=rgb_to_sh_dc(rows[:, 24:27].astype(np.float32)/255)[:, None, :],
                    shN=np.empty((len(rows), 0, 3), np.float32), sh_degree=0)


def build_object_meshes(run_dir: Path):
    from .colmap_io import read_model
    from .dataset import ViewSet
    from .mesh import build_mesh
    from plyfile import PlyData

    run_dir = Path(run_dir).resolve()
    root = run_dir / 'object-meshes'
    if root.is_symlink():
        raise ValueError('Refusing symlinked mesh output directory')
    root.mkdir(exist_ok=True)
    # OS lock releases on process exit, including crashes. It also covers CLI jobs.
    with (root / 'job.lock').open('a+b') as lock:
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            if not lock.read(1):
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Progress(root, 'object-mesh') as progress:
            progress.update(message='Validating separated splats')
            records = [r for r in load_validated_objects(run_dir / 'objects') if 'splat_path' in r]
            if not records:
                raise ValueError('No validated separated splats are available')
            for rec in records:
                if 'transform' in rec and not np.allclose(np.asarray(rec['transform']).reshape(4, 4), np.eye(4)):
                    raise ValueError('Meshing requires splats in original reconstruction coordinates; transformed assets are unsupported')
            model = read_model(run_dir / 'sfm' / 'sparse_text')
            progress.update(message='Loading registered camera views')
            views = ViewSet(model, run_dir / 'ingest' / 'images', long_edge=1200)
            if not len(views):
                raise ValueError('No registered source views are available')
            generation = uuid.uuid4().hex
            published = root / 'published'
            if published.is_symlink():
                raise ValueError('Refusing symlinked mesh publication directory')
            published.mkdir(exist_ok=True)
            results = []
            with tempfile.TemporaryDirectory(prefix='staging-', dir=root) as scratch:
                folder = Path(scratch)
                output = folder / 'result'
                output.mkdir()
                for index, rec in enumerate(records):
                    source = resolve_contained(rec['splat_path'], 'splat_path', run_dir / 'objects')
                    input_copy = folder / 'input.splat'
                    input_copy.write_bytes(source.read_bytes())
                    if sha256_file(input_copy) != rec['sha256']:
                        raise ValueError('Object changed during meshing; retry after separation finishes')
                    ply = folder / 'input.ply'
                    splat_to_ply(input_copy, ply)
                    mesh = output / f'object-{index:04d}.ply'
                    progress.update(message=f"Reconstructing surface: {rec['label']}", count=index, total=len(records))
                    build_mesh(ply, views, mesh, trim_fraction=0)
                    data = PlyData.read(mesh)
                    vertices = np.column_stack([data['vertex'][axis] for axis in ('x','y','z')])
                    faces = data['face']['vertex_indices']
                    if not len(vertices) or not len(faces) or not np.isfinite(vertices).all():
                        raise ValueError('Mesher produced an empty or invalid surface')
                    for face in faces:
                        if len(face) != 3 or np.any(face < 0) or np.any(face >= len(vertices)):
                            raise ValueError('Mesher produced invalid triangle indices')
                    results.append(dict(object_id=rec['object_id'], label=rec['label'],
                                        source_sha256=rec['sha256'], file=mesh.name,
                                        sha256=sha256_file(mesh), vertices=len(vertices), faces=len(faces)))
                manifest = dict(schema='vitrine/object-mesh/1', generation=generation,
                                method='expected-depth-screened-poisson', experimental=True,
                                parameters=dict(max_views=120, long_edge=1200, poisson_depth=10, trim_fraction=0),
                                cameras_sha256=sha256_file(run_dir / 'sfm' / 'sparse_text' / 'cameras.txt'),
                                images_sha256=sha256_file(run_dir / 'sfm' / 'sparse_text' / 'images.txt'),
                                appearance='vertex-colour from SH0 splat', objects=results)
                atomic_json(output / 'manifest.json', manifest)
                os.replace(output, published / generation)
                atomic_json(root / 'latest.json', manifest)
            progress.update(message='Mesh derivatives ready', count=len(records), total=len(records))
    return results
