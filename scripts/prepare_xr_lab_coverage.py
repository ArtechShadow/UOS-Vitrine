"""Stage all non-blurred audited XR Lab frames into an isolated coverage run."""
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vitrine.ingest import CameraGroup, stage_group
from vitrine.construction import Progress, atomic_json


def main():
    source = ROOT / "runs" / "xr-lab-rtx-a6000-video-test"
    target = ROOT / "runs" / "xr-lab-coverage-03"
    if target.exists():
        raise SystemExit("Coverage run exists; refusing overwrite")
    audit = json.loads((source / "quality-comparisons/frame-audit.json").read_text())
    chosen = [r for r in audit["records"] if r["selected"] or r["sharpness"] >= audit["threshold"]]
    name = "video_20260909_133903000_iOS"
    target.mkdir()
    atomic_json(target / "capture.json", {"title":"XR Lab — expanded frame coverage", "capture_type":"scene",
                "source_run":source.name, "note":"Quality challenger, not yet validated"})
    baseline_eval = json.loads((source / "model/evaluation.json").read_text())
    atomic_json(target / "coverage-recipe.json", {
        "source_run":source.name, "frames":chosen, "threshold":audit["threshold"],
        "held_out_names":[r["name"] for r in baseline_eval["views"]],
        "requirement":"Exclude these same held-out names from training for a valid comparison"})
    group = CameraGroup(name,3840,2160,from_video=True)
    with Progress(target / "ingest", "ingest") as progress:
        folder = target / "ingest/images" / name
        folder.mkdir(parents=True)
        for index, record in enumerate(chosen,1):
            path = source / record["file"]
            original = source / "ingest/images" / name / (path.stem + ".jpg")
            if original.is_file():
                shutil.copy2(original, folder / original.name)
            elif stage_group(group,[path],target / "ingest/images",long_edge=3200) != 1:
                raise RuntimeError(f"Failed to stage {path}")
            progress.update(count=index,total=len(chosen),unit="staged images")
            if index % 50 == 0:
                print(f"Staged {index}/{len(chosen)}",flush=True)
        atomic_json(target / "ingest/ingest.json", {
            "accepted":len(chosen),"rejected":audit["count"]-len(chosen),
            "groups":[{"name":name,"width":3200,"height":1800,"count":len(chosen),"from_video":True}],
            "notes":["Original 200 staged JPEGs copied unchanged; additional audited frames added."]})
    print(target,flush=True)


if __name__ == "__main__":
    main()
