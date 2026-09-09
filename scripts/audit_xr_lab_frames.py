"""Read-only sharpness audit of retained XR Lab video frames; cache evidence."""
import json
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vitrine.ingest import sharpness
from vitrine.construction import atomic_json


def main():
    import cv2
    cv2.setNumThreads(1)
    run = ROOT / "runs" / "xr-lab-rtx-a6000-video-test"
    output = run / "quality-comparisons" / "frame-audit.json"
    if output.exists():
        raise SystemExit("Audit exists; refusing to overwrite evidence")
    paths = sorted((run / "ingest" / "_video_frames").rglob("*.png"))
    selected = {p.stem for p in (run / "ingest" / "images").rglob("*.jpg")}
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for path, score in zip(paths, pool.map(sharpness, paths)):
            rows.append(dict(file=path.relative_to(run).as_posix(), sharpness=score,
                             selected=path.stem in selected))
            if len(rows) % 50 == 0:
                print(f"Scored {len(rows)}/{len(paths)}", flush=True)
    ordered = sorted(rows, key=lambda row: row["sharpness"])
    threshold = ordered[int(len(rows)*.15)]["sharpness"] if rows else 0
    extras = [r for r in rows if not r["selected"] and r["sharpness"] >= threshold]
    result = dict(count=len(rows), threshold=threshold, additional_nonbottom_frames=len(extras),
                  note="Sharpness alone does not prove added geometric coverage", records=rows)
    atomic_json(output, result)
    print(json.dumps({k:v for k,v in result.items() if k != "records"}), flush=True)


if __name__ == "__main__":
    main()
