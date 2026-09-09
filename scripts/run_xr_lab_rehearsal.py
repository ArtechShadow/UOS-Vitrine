"""Reproducible, capture-specific HQ rehearsal; keeps shipped profiles unchanged."""
from pathlib import Path
import functools
import hashlib
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ["VITRINE_LIVE_PREVIEWS"] = "1"

def main():
    import torch
    import vitrine.train as trainer
    import vitrine.sfm as sfm
    from vitrine.cli import main as cli
    from vitrine.construction import atomic_json
    torch.set_num_threads(8)
    run = ROOT / "runs/xr-lab-20260909-hq"
    source = ROOT / "source/xr-lab-20260909/20260909_133903000_iOS.MOV"
    trainer.APPEARANCE_OPT = False
    if os.environ.get('VITRINE_XR_OPACITY_REG'):
        trainer.OPACITY_REG = float(os.environ['VITRINE_XR_OPACITY_REG'])
        atomic_json(run/'opacity-trial.json', dict(opacity_reg=trainer.OPACITY_REG,
                    scale_reg=trainer.SCALE_REG, seed=0,
                    reason='Matched XR Lab trial after default opacity penalty caused measured detail loss'))
    trainer.OPACITY_REG_UNTIL_REFINE_STOP = True
    trainer.MAX_ANISOTROPY = 100.0
    trainer.MAX_SCALE_FRACTION = 0.25
    trainer.LR_DECAY_HORIZON_STEPS = 15000
    trainer.train = functools.partial(trainer.train, save_every=5000)
    # Retain the completed global pairs and add video-neighbour pairs. The
    # saved database supplies measured long-range matches for loop closure.
    sfm.EXHAUSTIVE_LIMIT = 300
    colmap_command = sfm._run
    def video_match_command(arguments, **kwargs):
        if arguments[0] == 'sequential_matcher':
            arguments = list(arguments)
            arguments[arguments.index('--SequentialMatching.loop_detection') + 1] = '0'
            arguments[arguments.index('--SequentialMatching.overlap') + 1] = '20'
        return colmap_command(arguments, **kwargs)
    sfm._run = video_match_command
    recipe = dict(schema="vitrine/xr-lab-rehearsal/1", created=time.time(),
                  source=str(source), source_sha256=hashlib.file_digest(source.open("rb"),"sha256").hexdigest(),
                  profile="demo", iterations=30000, source_long_edge=2304,crop=1536,
                  cap_max=2000000,sh_degree=3,appearance_opt=False,opacity_release=True,
                  max_anisotropy=100,max_scale_fraction=.25,lr_horizon=15000,
                  checkpoint_every=5000,selection_preset="archive",sfm_matching="exhaustive",
                  code_sha256={name:hashlib.sha256((ROOT/"vitrine"/name).read_bytes()).hexdigest()
                               for name in ("ingest.py","train.py","sfm.py","dataset.py","cli.py","pipeline.py")})
    if not (run/"rehearsal-recipe.json").exists():
        atomic_json(run/"rehearsal-recipe.json",recipe)
    args = ["--run-dir",str(run),"--quality","demo","run","--source",str(source),
            "--originals",str(source.parent),"--selection-preset","archive","--gpu","yes",
            "--iterations","30000","--eval-every","2500","--capture-type","scene",
            "--title","XR Lab — 9 September 2026 HQ rehearsal",
            "--subject","University of Salford XR Lab; full local rehearsal for 30 September 2026."]
    if "--resume" in sys.argv:
        args.append("--resume")
    return cli(args)

if __name__ == "__main__":
    raise SystemExit(main())
