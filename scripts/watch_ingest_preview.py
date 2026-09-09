"""Observe an older running ingest from its real output files, without changing selection."""
import argparse
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.construction import read_json
from vitrine.ingest_preview import IngestPreview


def watch(run):
    folder = run / 'ingest'
    existing = read_json(folder / 'selection.json')
    if existing and existing.get('observation') != 'Recovered from files produced by this run':
        raise SystemExit('A native selection record exists; leaving its owner in control.')
    preview = IngestPreview(folder, 0)
    preview.data['observation'] = 'Recovered from files produced by this run'
    if existing:
        preview.records = existing.pop('records', [])
        preview.data.update(existing)
        preview.by_path = {str(folder / '_video_frames' / r['group'].removeprefix('video_') / r['file']): r for r in preview.records}
    preview.data['scored'] = None
    while True:
        preview.data['source_frames'] = len(list((folder / '_video_frames').glob('*/*.png')))
        for frames in sorted((folder / '_video_frames').glob('*')):
            if frames.is_dir():
                preview.extracting(frames.name)
                for path in sorted(frames.glob('frame_*.png')):
                    preview.extracted('video_' + frames.name, path)
        for raw, record in preview.by_path.items():
            path = Path(raw)
            if record['status'] == 'pending' and (folder / 'images' / record['group'] / (path.stem + '.jpg')).is_file():
                preview.decision(record['group'], path, 'kept', 'Present in prepared images')
        report = read_json(folder / 'ingest.json')
        if report:
            reasons = {r['file']: r['reason'] for r in report.get('rejected_examples', [])}
            for raw, record in preview.by_path.items():
                if record['status'] != 'pending':
                    continue
                path = Path(raw)
                kept = (folder / 'images' / record['group'] / (path.stem + '.jpg')).is_file()
                preview.decision(record['group'], path, 'kept' if kept else 'rejected',
                                 'Present in prepared images' if kept else reasons.get(path.name, 'Not selected by this run; detailed reason was not recorded'))
            preview.data.update(phase='sorting', scored=preview.data['total'])
            preview.finish()
            print(f"Recorded {preview.data['kept']} kept / {preview.data['rejected']} rejected", flush=True)
            return
        status = read_json(folder / 'construction-status.json') or {}
        if status.get('state') == 'failed' or time.time() - status.get('heartbeat', 0) > 120:
            preview.data['phase'] = 'interrupted'
            preview.publish(force=True)
            print('Ingest stopped; retained previews without inferring decisions.', flush=True)
            return
        preview.publish(force=True)
        print(f"Observed {preview.data['extracted']} extracted frames", flush=True)
        time.sleep(2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    watch(parser.parse_args().run)
