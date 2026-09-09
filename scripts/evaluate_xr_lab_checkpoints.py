"""Compare saved XR Lab candidates on identical held-out photographs."""
from pathlib import Path
import hashlib
import json
import logging
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import torch
from vitrine.colmap_io import read_model
from vitrine.dataset import ViewSet
from vitrine.evaluate import evaluate_ply
from vitrine.export import render_previews
from vitrine.construction import Progress,atomic_json

def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    torch.set_num_threads(8)
    run=ROOT/'runs/xr-lab-20260909-hq'
    output=run/'quality-comparison'
    output.mkdir(exist_ok=False)
    model=read_model(run/'sfm/sparse_text')
    with Progress(output,'evaluate') as observer:
        views=ViewSet(model,run/'ingest/images',long_edge=2304,
                      on_load=lambda n,total:observer.update(count=n,total=total,unit='calibrated views'))
        names=[views.views[i].name for i in views.eval_indices]
        atomic_json(output/'evaluation-split.json',dict(names=names,long_edge=1600))
        candidates=[('default-opacity-15000',run/'attempts/02-default-opacity-collapse/model/checkpoint_15000.ply')]
        candidates += [(f'reduced-opacity-{p.stem.split("_")[-1]}',p)
                       for p in sorted((run/'model').glob('checkpoint_*.ply'),key=lambda p:int(p.stem.split('_')[-1]))]
        candidates.append(('reduced-opacity-30000',run/'model/scene.ply'))
        results=[]
        for index,(label,path) in enumerate(candidates):
            if not path.is_file():
                continue
            observer.update(count=index,total=len(candidates),unit='saved candidates',message=label)
            started=time.time()
            report=evaluate_ply(path,views,max_long_edge=1600)
            doc=json.loads(report.to_json())
            atomic_json(output/(label+'.json'),doc)
            record=dict(label=label,path=str(path),sha256=hashlib.file_digest(path.open('rb'),'sha256').hexdigest(),
                        psnr=report.overall_psnr,ssim=report.overall_ssim,views=len(report.views),seconds=time.time()-started)
            results.append(record)
            print(json.dumps(record),flush=True)
            atomic_json(output/'comparison.json',dict(candidates=results,selection='pending visual review'))
        best=max((r for r in results if r['label'].startswith('reduced')),key=lambda r:r['ssim'])
        indices=[views.eval_indices[round(i*(len(views.eval_indices)-1)/7)] for i in range(8)]
        render_previews(Path(best['path']),output/'best-previews',views,indices=indices,max_long_edge=1600)
        atomic_json(output/'comparison.json',dict(candidates=results,highest_ssim=best,
                    selection='pending visual review',evaluation='all identical held-out views at 1600px'))

if __name__=='__main__':
    main()
