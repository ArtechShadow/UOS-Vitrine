"""Bake the published XR Lab surfaces sequentially with local Blender OptiX."""
from pathlib import Path
import json
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vitrine.construction import Progress,atomic_json
from vitrine.pipeline import digest_file

def main():
    run=ROOT/'runs/xr-lab-20260909-hq'
    meshes=json.loads((run/'object-meshes/latest.json').read_text())
    root=run/'pbr'
    root.mkdir(exist_ok=False)
    blender=Path('C:/Program Files/Blender Foundation/Blender 5.1/blender.exe')
    records=[]
    with Progress(root,'materials') as progress:
        for i,record in enumerate(meshes['objects']):
            oid=record['object_id']
            source=run/'object-meshes/published'/meshes['generation']/record['file']
            if digest_file(source)!=record['sha256']:
                raise ValueError('Published source mesh changed')
            camera=json.loads((run/'objects'/oid/'viewer-cameras.json').read_text())[0]
            progress.update(count=i,total=len(meshes['objects']),message=f"Baking 4K PBR maps: {record['label']}")
            out=root/oid
            command=[str(blender),'--background','--factory-startup','--python',str(ROOT/'scripts/bake_object_pbr_blender.py'),
                     '--','--input',str(source),'--output',str(out),'--label',record['label'],'--resolution','4096',
                     '--faces','200000','--roughness','.85' if oid=='upholstered-seat' else '.6',
                     '--up','-.013702719','-.999443584','-.030409834','--camera-position',*map(str,camera['position'])]
            with (root/(oid+'.log')).open('wb') as log:
                subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
            subprocess.run([sys.executable,str(ROOT/'scripts/validate_pbr_glb.py'),str(out/'model.glb'),
                            '--report',str(out/'validation.json')],check=True)
            records.append(dict(object_id=oid,label=record['label'],source_mesh=record,
                                glb_path=str(out/'model.glb'),sha256=digest_file(out/'model.glb'),
                                review='pending visual inspection'))
            atomic_json(root/'manifest.json',dict(schema='vitrine/pbr-objects/1',objects=records))
        progress.update(count=len(records),total=len(records),message='PBR GLBs baked and structurally checked; visual review pending')

if __name__=='__main__':
    main()
