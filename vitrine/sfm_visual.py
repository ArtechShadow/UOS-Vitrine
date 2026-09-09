"""Expose COLMAP's flushed feature logs without opening its database."""
from pathlib import Path
import json
import re
from urllib.parse import quote


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
            try:
                result = json.loads((run_dir / 'sfm/features-preview.json').read_text())
            except (OSError, ValueError):
                result = dict(image=None, points=[], features=0, processed=0)
            if matching:
                result['matching'] = matching
            return result if result.get('image') or matching else None
        count, name, width, height, features = (latest[k] for k in ('count', 'name', 'width', 'height', 'features'))
        source = (run_dir / 'ingest/images' / name).resolve()
        if not source.is_relative_to((run_dir / 'ingest/images').resolve()):
            return None
        return dict(image=name, width=int(width), height=int(height), features=int(features), matching=matching,
                    processed=int(count), points=[], source='COLMAP log',
                    url='/files/' + quote(run_dir.name, safe='') + '/ingest/images/' + quote(name, safe='/'))
    except (OSError, ValueError):
        return None
