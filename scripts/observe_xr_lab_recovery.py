"""Forward real, closed recovery snapshots to the current construction viewer."""
from pathlib import Path
import shutil
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vitrine.construction import SnapshotStore, read_json, snapshot_records, atomic_json
run = ROOT/'runs/xr-lab-20260909-hq'
source = run/'sfm-recovery-simple-radial/construction/live'
store = SnapshotStore(run)
seen = {r.get('source_snapshot') for r in store.records}
largest = max((r.get('registered',0) for r in store.records),default=0)
# Keep the main connected component visible while COLMAP tries smaller islands.
kept = []
previous = 0
for record in store.records:
    if record.get('registered',0) >= previous:
        kept.append(record)
        previous = record.get('registered',0)
if kept != store.records:
    atomic_json(run/'evidence/recovery-preview-history-all-components.json',dict(snapshots=store.records))
    store.records=kept
    atomic_json(store.manifest,dict(snapshots=kept))
while True:
    for record in snapshot_records(source/'manifest.json'):
        if record['id'] in seen:
            continue
        if record.get('registered',0) < largest:
            seen.add(record['id'])
            continue
        path = source/record['id']
        if path.is_file():
            store.publish('sparse','.json',lambda target: shutil.copyfile(path,target),
                          registered=record.get('registered'),points=record.get('points'),
                          source_snapshot=record['id'],source_created=record['created'])
            seen.add(record['id'])
            largest=record.get('registered',0)
    if (read_json(run/'pipeline.json') or {}).get('state') != 'running':
        break
    time.sleep(3)
