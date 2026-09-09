"""Record measured alignment review and preserve the rejected first attempt."""
from pathlib import Path
import json
import sys
import time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vitrine.colmap_io import read_model
from vitrine.construction import atomic_json,SnapshotStore,sparse_payload
from vitrine.pipeline import run_lock,fingerprint
from vitrine.sfm import validate_registration

run=ROOT/'runs/xr-lab-20260909-hq'
recovery=run/'sfm-recovery-simple-radial'
with run_lock(run):
    state=json.loads((run/'pipeline.json').read_text())
    assert state['state']=='cancelled' and state['pid'] is None
    model=read_model(recovery/'sparse_text')
    validate_registration(len(model.images),521)
    assert len(model.images)==448 and len(model.points_xyz)==80800
    assert np.isfinite(model.points_xyz).all()
    ids=[int(im.name.split('_')[-1].split('.')[0]) for im in model.images]
    errors=[]
    tracks=[]
    for line in (recovery/'sparse_text/points3D.txt').open():
        if line.startswith('#') or not line.strip():
            continue
        fields=line.split()
        errors.append(float(fields[7]))
        tracks.append((len(fields)-8)//2)
    camera=next(iter(model.cameras.values()))
    assert 2000 < camera.fx < 3500 and abs(camera.params['k1']) < .2
    report=dict(schema='vitrine/alignment-review/1',registered=len(model.images),total=521,
                coverage=len(model.images)/521,points=len(model.points_xyz),
                cameras=[dict(id=c.id,model=c.model,params=c.params) for c in model.cameras.values()],
                temporal_bins=np.histogram(ids,bins=np.linspace(1,523,11))[0].tolist(),
                mean_reprojection_error=float(np.mean(errors)),median_reprojection_error=float(np.median(errors)),
                p95_reprojection_error=float(np.percentile(errors,95)),mean_track_length=float(np.mean(tracks)),
                bundle_adjustment='CONVERGENCE',accepted_for_training=True,
                visual_evidence='evidence/browser; actual live sparse room and camera path inspected',
                limitations=['73 prepared views did not register; coverage is 86%, not complete.',
                             'Sparse alignment is not a final quality assessment of the splat or object meshes.'])
    atomic_json(run/'evidence/alignment-review.json',report)
    old=(run/'sfm').resolve()
    rejected=(run/'attempts/01-rejected-two-camera-model/sfm').resolve()
    assert old.is_relative_to(run.resolve()) and rejected.is_relative_to(run.resolve())
    old.rename(rejected)
    assert recovery.resolve().is_relative_to(run.resolve())
    recovery.rename(old)
    stats=dict(registered_images=len(model.images),cameras=len(model.cameras),points=len(model.points_xyz),
               used_gpu=True,sparse_dir=str(old/'sparse_text'),selected_component='1',camera_model='SIMPLE_RADIAL',
               input_images=521,registration_fraction=len(model.images)/521)
    atomic_json(old/'sfm.json',stats)
    snapshot=sparse_payload(model)
    SnapshotStore(run).publish('sparse','.json',lambda path:atomic_json(path,snapshot),
                              registered=len(model.images),points=len(model.points_xyz),final=True)
    atomic_json(old/'construction-status.json',dict(stage='sfm',state='complete',heartbeat=time.time(),
                started=state['stages']['sfm']['started'],registered=len(model.images),points=len(model.points_xyz),
                message='Reviewed primary reconstruction: 448 of 521 views; calibration refinement converged.'))
    state['stages']['sfm'].update(state='complete',seconds=time.time()-state['stages']['sfm']['started'],
                                outputs=fingerprint(run,'sfm'),registered=len(model.images),review=str(run/'evidence/alignment-review.json'))
    state['stages']['train']=dict(state='pending')
    state.update(error=None,updated=time.time())
    atomic_json(run/'pipeline.json',state)
    print(json.dumps(report,indent=2))
