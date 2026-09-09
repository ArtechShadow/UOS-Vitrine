"""Select the visually identified cabinet and upholstered seat; preserve candidates."""
from pathlib import Path
import copy
import json
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from vitrine.objects import load_validated_objects
from vitrine.construction import atomic_json

run=ROOT/'runs/xr-lab-20260909-hq'
source=run/'segmentation-refinement'
load_validated_objects(source)
records=json.loads((source/'objects.json').read_text())['objects']
selected=[]
out=run/'objects'
prior=run/'attempts/04-initial-object-separation'
prior.mkdir(exist_ok=True)
if out.resolve().parent != run.resolve():
    raise ValueError('Unexpected objects path')
if out.exists():
    out.rename(prior/'objects')
out.mkdir()
for old,new,label in [('candidate_01','wooden-cabinet','XR Lab wooden cabinet'),
                      ('candidate_05','upholstered-seat','XR Lab upholstered seat')]:
    record=copy.deepcopy(next(r for r in records if r['object_id']==old))
    shutil.copytree(source/old,out/new)
    record.update(object_id=new,label=label,splat_path=f'{new}/observed.splat',preview_path=f'{new}/preview.png')
    record['provenance'].update(selection_source=str(source/old),
                  review_status='source-identity-reviewed; isolated-surface-review-pending',
                  selection_reason='Identified in the real source crop, with the largest supported component for the intended object.')
    selected.append(record)
atomic_json(out/'objects.json',dict(schema='vitrine/object/1',objects=selected,
            method='Local GroundingDINO + SAM2.1 large; 160 registered frames; multiview carve',
            refinement_manifest=str(source/'objects.json')))
load_validated_objects(out)
print('Selected cabinet and upholstered seat; all initial and refinement candidates retained.')
