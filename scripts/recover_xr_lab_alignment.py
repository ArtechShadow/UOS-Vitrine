"""Preserved-database calibration retry, requiring review before training."""
from pathlib import Path
import json
import os
import sqlite3
import struct
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['VITRINE_LIVE_PREVIEWS'] = '1'
from vitrine import sfm
from vitrine.construction import Progress, atomic_json
from vitrine.pipeline import run_lock

def main():
    run = ROOT / 'runs/xr-lab-20260909-hq'
    work = run / 'sfm-recovery-simple-radial'
    work.mkdir(exist_ok=False)
    with run_lock(run):
        state = json.loads((run/'pipeline.json').read_text())
        assert state['state'] == 'cancelled', 'Previous process must be stopped'
        atomic_json(work/'pipeline.before.json', state)
        cancel = run/'cancel.request'
        if cancel.exists():
            cancel.rename(work/'cancel.before.request')
        # Keep invalid trained geometry and its snapshots out of the live result.
        failed = run/'attempts/01-rejected-two-camera-model'
        failed.mkdir(parents=True, exist_ok=False)
        for name in ('model', 'construction'):
            src, dst = (run/name).resolve(), (failed/name).resolve()
            assert src.is_relative_to(run.resolve()) and dst.is_relative_to(run.resolve())
            if src.exists():
                src.rename(dst)
        source = sqlite3.connect(run/'sfm/database.db')
        target = sqlite3.connect(work/'database.db')
        source.backup(target)
        source.close()
        old = []
        for cid, model, width, height, blob in target.execute('SELECT camera_id,model,width,height,params FROM cameras'):
            params = np.frombuffer(blob, dtype=np.float64)
            old.append(dict(id=cid, model=model, params=params.tolist()))
            new = np.array([params[0], width/2, height/2, 0.], dtype=np.float64)
            target.execute('UPDATE cameras SET model=2,params=?,prior_focal_length=0 WHERE camera_id=?', (new.tobytes(),cid))
        target.commit()
        target.close()
        atomic_json(work/'calibration-retry.json', dict(original=old, model='SIMPLE_RADIAL',
                    rationale='Rejected two-image OPENCV fit had grossly wrong focal lengths and distortion',
                    min_model_size=50, multiple_models=True, init_min_tri_angle=8))
        (work/'sparse').mkdir()
        mounts = {run/'ingest/images':'/images',work:'/work'}
        state.update(state='running',pid=os.getpid(),error=None,updated=time.time())
        state['stages']['sfm'] = dict(state='running', started=time.time(), recovery='SIMPLE_RADIAL retry')
        state['stages']['train'] = dict(state='pending', note='Previous two-camera model rejected')
        atomic_json(run/'pipeline.json',state)
        try:
            with Progress(run/'sfm','sfm') as observer:
                sfm._run(['mapper','--database_path','/work/database.db','--image_path','/images',
                          '--output_path','/work/sparse','--Mapper.multiple_models','1',
                          '--Mapper.max_num_models','10','--Mapper.min_model_size','50',
                          '--Mapper.init_min_tri_angle','8','--Mapper.max_focal_length_ratio','2',
                          '--Mapper.max_runtime_seconds','600','--Mapper.num_threads','8'],
                         mounts=mounts,image=sfm.DEFAULT_IMAGE,use_gpu=False,timeout=720,
                         log_path=work/'colmap.log',progress=observer)
                candidates = []
                for path in (work/'sparse').glob('*/images.bin'):
                    with path.open('rb') as stream:
                        n = struct.unpack('<Q',stream.read(8))[0]
                    candidates.append((n,path.parent))
                if not candidates:
                    raise RuntimeError('Recovery mapper produced no model')
                candidates.sort(reverse=True)
                n,best = candidates[0]
                atomic_json(work/'models.json',dict(models=[dict(images=c,path=str(p)) for c,p in candidates]))
                sfm.validate_registration(n,521)
                (work/'sparse_text').mkdir()
                for args in (
                    ['bundle_adjuster','--input_path','/work/'+best.relative_to(work).as_posix(),
                     '--output_path','/work/'+best.relative_to(work).as_posix(),'--BundleAdjustment.refine_principal_point','1'],
                    ['model_converter','--input_path','/work/'+best.relative_to(work).as_posix(),
                     '--output_path','/work/sparse_text','--output_type','TXT']):
                    sfm._run(args,mounts=mounts,image=sfm.DEFAULT_IMAGE,use_gpu=False,timeout=600,
                             log_path=work/'colmap.log',progress=observer)
                state.update(state='cancelled',error=f'Recovery registered {n}/521 images; awaiting measured alignment review')
                state['stages']['sfm'].update(state='cancelled',registered=n)
        except BaseException as exc:
            state.update(state='failed',error=str(exc))
            state['stages']['sfm'].update(state='failed',error=str(exc))
            raise
        finally:
            state.update(pid=None,updated=time.time())
            atomic_json(run/'pipeline.json',state)

if __name__ == '__main__':
    main()
