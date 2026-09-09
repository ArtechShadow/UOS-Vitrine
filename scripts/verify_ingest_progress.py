"""Verify visual reporting against real frames from a supplied run (no fixtures)."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vitrine.ingest import CameraGroup, select_sharpest, stage_group, extract_video_frames
from vitrine.ingest_preview import IngestPreview
from vitrine.construction import construction_payload
from vitrine.package import _copy_tree
from vitrine.sfm_visual import feature_preview


def verify(run):
    paths = sorted((run / 'ingest/_video_frames').glob('*/*.png'))[:8]
    if len(paths) < 8:
        raise SystemExit('Supply a real run with at least eight extracted frames.')
    root = Path(tempfile.mkdtemp(prefix='ingest-progress-', dir='tmp'))
    folder = root / 'ingest'
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    started = time.monotonic()
    feature_preview(run)
    assert time.monotonic() - started < 2, 'Live log parsing blocked the observer'
    group = CameraGroup('real-video', 3840, 2160, paths, from_video=True)
    expected = select_sharpest(group, 3)
    preview = IngestPreview(folder, len(paths))
    actual = select_sharpest(group, 3, on_scored=preview.scored)
    assert expected == actual, 'Reporting changed selection'
    kept, rejected = actual
    for path, score, reason in rejected:
        preview.decision(group.name, path, 'rejected', reason, score)
    written = stage_group(group, kept, folder / 'images', long_edge=800,
                         on_staged=lambda p, ok: preview.decision(group.name, p, 'kept' if ok else 'rejected', 'Prepared' if ok else 'Could not prepare'))
    preview.finish()
    record = json.loads((folder / 'selection.json').read_text())
    assert record['kept'] == written == 3 and record['rejected'] == 5
    assert len(record['records']) == 8 and record['complete']
    payload = construction_payload(root)
    assert all(r.get('url') for r in payload['selection']['records'])
    copied = []
    assert _copy_tree(folder / 'images', root / 'copy-check', description='real frames', on_copied=copied.append) == len(copied) == 3
    for p, digest in hashes.items():
        assert hashlib.sha256(p.read_bytes()).hexdigest() == digest, 'Source changed'
    videos = list((run / 'source').rglob('*.mov'))
    if videos:
        video_preview = IngestPreview(root / 'video-check', 0)
        video_preview.extracting(videos[0].name)
        frames = extract_video_frames(videos[0], root / 'video-check/frames', max_frames=4,
                                      on_frame=lambda p: video_preview.extracted('video', p))
        video_preview.publish(force=True)
        assert len(frames.paths) == video_preview.data['extracted'] == 4
        assert len(video_preview.records) == 4
    print('PASS: unchanged keep/reject decisions; actual thumbnails; source hashes intact; copy callbacks; video frame callbacks')
    print(root)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    verify(parser.parse_args().run)
