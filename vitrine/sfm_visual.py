"""Expose COLMAP's logs and committed visual evidence through read-only queries."""
from pathlib import Path
import json
import re
from urllib.parse import quote


def global_mapper_progress(run_dir):
    """Report solver log events; track counts are never labelled as 3D points."""
    try:
        with (Path(run_dir) / 'sfm/colmap.log').open('rb') as stream:
            stream.seek(max(0, stream.seek(0, 2) - 128000))
            tail = stream.read().decode('utf-8', errors='replace')
    except OSError:
        return None
    phases = {
        'rotation averaging': 'Solving camera orientations',
        'track establishment': 'Connecting matched features across photographs',
        'global positioning': 'Solving camera positions and scene structure',
        'iterative bundle adjustment': 'Refining camera poses and scene structure',
        'iterative retriangulation and refinement': 'Rechecking 3D points and refining the reconstruction',
    }
    result = None
    component = None
    for line in tail.splitlines():
        match = re.search(r'Reconstructing component (\d+) / (\d+) with (\d+) images', line)
        if match:
            component = dict(index=int(match[1]), total=int(match[2]), images=int(match[3]))
        match = re.search(r'=== Running (.*?) ===', line)
        if match and match[1] in phases:
            result = dict(phase=match[1], message=phases[match[1]], component=component)
        match = re.search(r'Global bundle adjustment iteration (\d+) / (\d+)(.*)', line)
        if match:
            result = dict(phase='iterative bundle adjustment', component=component,
                          iteration=int(match[1]), iterations=int(match[2]),
                          message=f'Refining camera poses: pass {match[1]} of {match[2]}'
                          + (' — orientations fixed' if 'fixed-rotation' in match[3] else ' — pass finished'))
        if 'Extracting colors ...' in line:
            result = dict(phase='extracting colors', message='Adding photograph colours to the reconstructed points')
        if 'colmap model_converter --input_path' in line:
            result = dict(phase='exporting', message='Saving the camera solution and preparing its 3D preview')
    if result:
        result['preview_pending'] = True
        result['message'] += '. The global solver publishes its 3D preview after refinement.'
    return result


def mapper_preview(run_dir):
    """Resolve actual mapper log activity to its captured photograph."""
    import sqlite3
    root = Path(run_dir).resolve()
    try:
        with (root / 'sfm/colmap.log').open('rb') as stream:
            stream.seek(max(0, stream.seek(0, 2) - 64000))
            tail = stream.read().decode('utf-8', errors='replace')
        attempts = list(re.finditer(r'Registering image #(\d+) \(num_reg_(?:frames|images)=(\d+)\)', tail))
        if not attempts:
            return None
        last = attempts[-1]
        shared = re.search(r'Image sees (\d+) / (\d+) points', tail[last.end():])
        connection = sqlite3.connect((root / 'sfm/database.db').as_uri() + '?mode=ro', uri=True, timeout=.05)
        try:
            row = connection.execute('SELECT name FROM images WHERE image_id=?', (int(last[1]),)).fetchone()
        finally:
            connection.close()
        if not row or not (root / 'ingest/images' / row[0]).resolve().is_relative_to(root / 'ingest/images'):
            return None
        return dict(image_id=int(last[1]), registered=int(last[2]), name=row[0],
                    shared_points=int(shared[1]) if shared else None,
                    visible_points=int(shared[2]) if shared else None,
                    refining='Global bundle adjustment' in tail[last.end():],
                    url='/files/' + quote(root.name, safe='') + '/ingest/images/' + quote(row[0], safe='/'))
    except (OSError, sqlite3.Error, ValueError):
        return None


def matching_preview(run_dir):
    """Read one committed, geometrically verified pair without modifying COLMAP."""
    import sqlite3
    import numpy as np
    from PIL import Image

    root = Path(run_dir).resolve()
    database = root / 'sfm/database.db'
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=0.05)
        try:
            connection.execute('PRAGMA query_only=ON')
            count = connection.execute('SELECT COUNT(*) FROM two_view_geometries WHERE rows > 0').fetchone()[0]
            pair = connection.execute('SELECT pair_id, rows, data FROM two_view_geometries WHERE rows > 0 ORDER BY pair_id DESC LIMIT 1').fetchone()
            if not pair:
                return dict(verified_pairs=count)
            ids = divmod(pair[0], 2147483647)
            matches = np.frombuffer(pair[2], dtype='<u4').reshape(-1, 2)
            matches = matches[np.linspace(0, len(matches) - 1, min(60, len(matches)), dtype=int)]
            images, points = [], []
            for side, image_id in enumerate(ids):
                name = connection.execute('SELECT name FROM images WHERE image_id=?', (image_id,)).fetchone()[0]
                source = (root / 'ingest/images' / name).resolve()
                if not source.is_relative_to(root / 'ingest/images'):
                    return None
                with Image.open(source) as image:
                    width, height = image.size
                cols, blob = connection.execute('SELECT cols, data FROM keypoints WHERE image_id=?', (image_id,)).fetchone()
                keypoints = np.frombuffer(blob, dtype='<f4').reshape(-1, cols)
                points.append(keypoints[matches[:, side], :2].tolist())
                images.append(dict(name=name, width=width, height=height,
                    url='/files/' + quote(root.name, safe='') + '/ingest/images/' + quote(name, safe='/')))
            return dict(verified_pairs=count, pair_id=str(pair[0]), inliers=pair[1], images=images,
                        correspondences=[[*left, *right] for left, right in zip(*points)])
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError, IndexError, TypeError):
        return None


def _committed_features(run_dir, name=None):
    """Read saved keypoints without waiting on or modifying COLMAP's writer."""
    import sqlite3
    import numpy as np

    root = Path(run_dir).resolve()
    database = root / 'sfm/database.db'
    if not database.is_file():
        return None
    try:
        connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.05)
        try:
            connection.execute('PRAGMA query_only=ON')
            query = '''SELECT images.name, cameras.width, cameras.height,
                              keypoints.rows, keypoints.cols, keypoints.data
                       FROM keypoints JOIN images USING(image_id)
                       JOIN cameras USING(camera_id) WHERE keypoints.rows > 0'''
            row = connection.execute(query + ' AND images.name=?', (name,)).fetchone() if name else None
            if row is None:
                # The log can advance before its transaction is committed.
                row = connection.execute(query + ' ORDER BY images.image_id DESC LIMIT 1').fetchone()
            processed = connection.execute('SELECT COUNT(*) FROM keypoints').fetchone()[0]
        finally:
            connection.close()
        if row is None:
            return None
        name, width, height, count, cols, blob = row
        source = (root / 'ingest/images' / name).resolve()
        if not source.is_relative_to(root / 'ingest/images') or cols < 2:
            return None
        points = np.frombuffer(blob, dtype='<f4').reshape(count, cols)[:, :2]
        # Bound browser work while showing only measured locations, never a grid.
        indices = np.linspace(0, count - 1, min(count, 1200), dtype=int)
        points = points[indices]
        points = points[np.isfinite(points).all(axis=1)]
        return dict(image=name, width=width, height=height, features=count,
                    points=points.tolist(), processed=processed, source='COLMAP committed keypoints',
                    url='/files/' + quote(root.name, safe='') + '/ingest/images/' + quote(name, safe='/'))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


def feature_preview(run_dir):
    run_dir = Path(run_dir)
    try:
        with (run_dir / 'sfm/colmap.log').open('rb') as stream:
            stream.seek(max(0, stream.seek(0, 2) - 64000))
            tail = stream.read().decode('utf-8', errors='replace')
        current, latest, matching = {}, None, None
        for line in tail.splitlines():
            block = re.search(r'(?:Processing|Matching) block \[(\d+)/(\d+),\s*(\d+)/(\d+)\]', line)
            if block:
                matching = dict(row=int(block[1]), rows=int(block[2]), column=int(block[3]), columns=int(block[4]))
            processed = re.search(r'Processed file \[(\d+)/(\d+)\]', line)
            if processed:
                current = {'count': processed[1]}
            elif 'Name:' in line:
                current['name'] = line.split('Name:', 1)[1].strip()
            elif 'Dimensions:' in line:
                dimensions = re.search(r'Dimensions:\s+(\d+)\s*x\s*(\d+)', line)
                if dimensions:
                    current.update(width=dimensions[1], height=dimensions[2])
            elif 'Features:' in line:
                features = re.search(r'Features:\s+(\d+)', line)
                if features and all(key in current for key in ('count', 'name', 'width', 'height')):
                    latest = {**current, 'features': features[1]}
        if not latest:
            committed = _committed_features(run_dir)
            if committed:
                return dict(committed, matching=matching)
            try:
                result = json.loads((run_dir / 'sfm/features-preview.json').read_text())
            except (OSError, ValueError):
                result = dict(image=None, points=[], features=0, processed=0)
            if matching:
                result['matching'] = matching
            return result if result.get('image') or matching else None
        count, name, width, height, features = (latest[k] for k in ('count', 'name', 'width', 'height', 'features'))
        committed = _committed_features(run_dir, name)
        if committed:
            return dict(committed, processed=int(count), matching=matching)
        source = (run_dir / 'ingest/images' / name).resolve()
        if not source.is_relative_to((run_dir / 'ingest/images').resolve()):
            return None
        return dict(image=name, width=int(width), height=int(height), features=int(features), matching=matching,
                    processed=int(count), points=[], source='COLMAP log',
                    url='/files/' + quote(run_dir.name, safe='') + '/ingest/images/' + quote(name, safe='/'))
    except (OSError, ValueError):
        return None
