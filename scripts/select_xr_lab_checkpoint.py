"""Promote the reviewed, independently measured XR Lab checkpoint; retain history."""
from pathlib import Path
import json
import shutil
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vitrine.construction import atomic_json
from vitrine.pipeline import fingerprint, digest_file
from vitrine.ply import read_splat_ply
from vitrine.colmap_io import read_model, scene_scale
from prepare_viewer_cameras import prepare


def main():
    run = ROOT / 'runs/xr-lab-20260909-hq'
    comparison = json.loads((run/'quality-comparison/comparison.json').read_text())
    best = comparison['highest_ssim']
    source = Path(best['path'])
    if digest_file(source) != best['sha256']:
        raise ValueError('Measured checkpoint changed')
    model_dir = run/'model'
    if (model_dir/'scene.ply').exists():
        raise ValueError('Preserve an existing master before selecting another candidate')
    evidence = run/'attempts/03-interrupted-final-save'
    evidence.mkdir(exist_ok=False)
    for name in ('pipeline.json', 'model/progress.json', 'model/construction-status.json'):
        shutil.copy2(run/name, evidence/Path(name).name)
    step = int(source.stem.split('_')[-1])
    shutil.copy2(source, model_dir/'scene.ply')
    report = json.loads((run/'quality-comparison'/f"{best['label']}.json").read_text())
    atomic_json(model_dir/'evaluation.json', report)
    splat = read_splat_ply(source)
    opacity = 1/(1+np.exp(-splat['opacities']))
    alive = opacity > .005
    anisotropy = np.exp(np.ptp(splat['scales'][alive], axis=1))
    previous = json.loads((model_dir/'progress.json').read_text())
    poses = read_model(run/'sfm/sparse_text')
    selection = dict(selected=best, requested_steps=30000, delivered_steps=step,
                     reason='Highest PSNR and SSIM among all intact saved candidates on the identical 56-view evaluation; eight spread render pairs visually inspected.',
                     limitation='30k optimization reached but process exited during final save. No final master survived. Cause unconfirmed.',
                     visual_review='Recognizable captured rooms and equipment. Thin structures and low-coverage views remain blurred or streaked; exterior and unseen surfaces are not validated.',
                     reviewed_at=time.time())
    atomic_json(run/'quality-comparison/selection.json', selection)
    record = dict(profile='demo', iterations=step, requested_iterations=30000,
                  n_train_views=392, n_eval_views=56, n_gaussians=splat['count'],
                  scene_scale=scene_scale(poses), sh_degree=3,
                  minutes=round((source.stat().st_mtime-json.loads((run/'pipeline.json').read_text())['stages']['train']['started'])/60,2),
                  timing_scope='Stage start through saved checkpoint, includes view preparation',
                  peak_vram_gb=None, final_psnr=best['psnr'], final_ssim=best['ssim'],
                  export_psnr=best['psnr'], export_ssim=best['ssim'],
                  metric_scope='Independently evaluated saved checkpoint; raw in-memory score unavailable for this step',
                  alive_fraction=float(alive.mean()), live_anisotropy_median=float(np.median(anisotropy)),
                  history=[h for h in previous['history'] if h['step']<=step],
                  ply_path=str(model_dir/'scene.ply'), recovery=selection)
    atomic_json(model_dir/'train.json', record)
    state = json.loads((run/'pipeline.json').read_text())
    state.update(state='failed', pid=None, updated=time.time(), error='Final-save interruption recovered from independently reviewed checkpoint; export and package pending.')
    state['config']['iterations'] = step
    state['profile']['iterations'] = step
    state['stages']['train'].update(state='complete', outputs=fingerprint(run,'train'), recovery=selection)
    state['stages']['evaluate'] = dict(state='complete', outputs=fingerprint(run,'evaluate'),
                                      method='56 identical held-out views; checkpoint comparison')
    atomic_json(run/'pipeline.json', state)
    names = [next(v.name for v in poses.images if Path(v.name).stem == f'frame_{i:05d}')
             for i in (65,129,273,515)]
    prepare(run,names,['XR Lab display and workstations','Acoustic panels and chair','XR Lab corridor','Desktop equipment'])
    print(json.dumps(selection,indent=2))

if __name__ == '__main__':
    main()
