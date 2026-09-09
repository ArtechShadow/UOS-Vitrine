"""Isolated exhaustive camera solve for the expanded XR Lab frame set."""
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vitrine import sfm


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    run = ROOT / "runs/xr-lab-coverage-03"
    if not (run / "ingest/ingest.json").is_file():
        raise SystemExit("Staging must finish before reconstruction")
    if (run / "sfm").exists():
        raise SystemExit("SfM directory exists: inspect existing job before resuming")
    # Per-process experiment override only. Exhaustive matching retains loop
    # closure without a missing vocabulary-tree dependency at >300 frames.
    sfm.EXHAUSTIVE_LIMIT = 500
    result = sfm.run_sfm(run / "ingest/images", run / "sfm", max_image_size=3200)
    print(result, flush=True)
