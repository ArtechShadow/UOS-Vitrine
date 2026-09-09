"""Isolated XR Lab challenger using the measured Nested Cinema master recipe.

Preserves original inputs and baseline; never overwrites an existing candidate.
The recipe is a starting hypothesis on XR Lab, not a claim of validated quality.
"""
from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_nested_cinema_04_master as master


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--high-resolution", action="store_true",
                        help="Test 3200px sources / 2176px crops against quality-01")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    master.SOURCE_RUN = master.ROOT / "runs" / "xr-lab-rtx-a6000-video-test"
    master.RUN_NAME = "xr-lab-quality-02-3200" if args.high_resolution else "xr-lab-quality-01"
    master.RUN_DIR = master.SOURCE_RUN / "experiments" / master.RUN_NAME
    # Pin the validated recipe rather than inherit unrelated shell overrides.
    master.SOURCE_LONG_EDGE = 3200 if args.high_resolution else 2304
    master.CROP = 2176 if args.high_resolution else 1536
    if master.RUN_DIR.exists():
        raise SystemExit(f"Candidate already exists; refusing overwrite: {master.RUN_DIR}")
    return master.main()


if __name__ == "__main__":
    raise SystemExit(main())
