"""Attach read-only feature reporting to a dashboard already running."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.construction import atomic_json, read_json
from vitrine.sfm_visual import feature_preview

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('run', type=Path)
args = parser.parse_args()
last = None
while True:
    state = read_json(args.run / 'sfm/construction-status.json') or {}
    preview = feature_preview(args.run)
    fingerprint = (preview.get('image'), tuple((preview.get('matching') or {}).values())) if preview else None
    if preview and fingerprint != last:
        atomic_json(args.run / 'sfm/features-preview.json', preview)
        last = fingerprint
        print(f"{preview['processed']} images with features", flush=True)
    if state.get('state') != 'running':
        break
    time.sleep(3)
