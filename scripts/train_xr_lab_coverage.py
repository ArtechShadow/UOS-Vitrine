"""Train the expanded camera solve while preserving the original holdout."""
import json
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_nested_cinema_04_master as master
from vitrine.dataset import ViewSet


class FixedHoldoutViews(ViewSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        recipe = json.loads((master.SOURCE_RUN / "coverage-recipe.json").read_text())
        names = set(recipe["held_out_names"])
        present = {v.name for v in self.views}
        if names - present:
            raise RuntimeError(f"Original holdout cameras not registered: {sorted(names-present)}")
        self.eval_indices = [i for i,v in enumerate(self.views) if v.name in names]
        self.train_indices = [i for i,v in enumerate(self.views) if v.name not in names]
        self.sample_weights = self._sampling_weights()
        assert len(self.eval_indices) == 23
        assert not set(self.eval_indices) & set(self.train_indices)
        logging.info("Fixed comparison split: %d training / %d original held-out views",
                     len(self.train_indices), len(self.eval_indices))
        (master.RUN_DIR / "fixed-holdout.json").write_text(json.dumps({
            "eval_names":sorted(names), "train_names":[self.views[i].name for i in self.train_indices],
            "source_camera_solve":str(master.SOURCE_RUN / "sfm/sparse_text")},indent=2),encoding="utf-8")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    master.SOURCE_RUN = master.ROOT / "runs/xr-lab-coverage-03"
    master.RUN_NAME = "xr-lab-coverage-quality-01"
    master.RUN_DIR = master.SOURCE_RUN / "experiments" / master.RUN_NAME
    if master.RUN_DIR.exists():
        raise SystemExit("Candidate exists; refusing overwrite")
    if not (master.SOURCE_RUN / "sfm/sfm.json").is_file():
        raise SystemExit("Camera solve must finish first")
    master.SOURCE_LONG_EDGE = 2304
    master.CROP = 1536
    master.ViewSet = FixedHoldoutViews
    return master.main()


if __name__ == "__main__":
    raise SystemExit(main())
