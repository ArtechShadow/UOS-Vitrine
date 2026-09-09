"""Reconstruct the XR Lab scene surface from its measured full-SH master."""
from pathlib import Path
import json
import logging
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import torch
from vitrine.colmap_io import read_model
from vitrine.dataset import ViewSet
from vitrine.mesh import build_mesh
from vitrine.mesh_glb import write_mesh_glb
from vitrine.construction import Progress,atomic_json
from vitrine.pipeline import digest_file

def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    torch.set_num_threads(8)
    run=ROOT/'runs/xr-lab-20260909-hq'
    output=run/'scene-mesh'
    output.mkdir(exist_ok=False)
    started=time.time()
    with Progress(output,'scene-mesh') as progress:
        model=read_model(run/'sfm/sparse_text')
        views=ViewSet(model,run/'ingest/images',long_edge=1600,
                      on_load=lambda n,total:progress.update(count=n,total=total,message='Preparing calibrated depth-fusion views'))
        progress.update(count=None,total=None,message='Fusing 160 camera views into a scene surface')
        source=run/'model/scene.ply'
        path=build_mesh(source,views,output/'xr-lab-surface.ply',max_views=160,max_long_edge=1600,depth=10)
        glb=write_mesh_glb(path,output/'xr-lab-surface.glb')
        atomic_json(output/'manifest.json',dict(schema='vitrine/scene-mesh/1',method='expected-depth-screened-poisson',
                    source_sha256=digest_file(source),max_views=160,long_edge=1600,poisson_depth=10,
                    outputs={p.name:digest_file(p) for p in (path,glb)},seconds=time.time()-started,
                    limitations=['Arbitrary capture scale','Surface inference from Gaussian expected depth; not survey geometry',
                                 'Transparent or unseen areas and Poisson bridges require visual review'],review='pending'))
if __name__=='__main__':
    main()
