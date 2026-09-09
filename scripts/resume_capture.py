"""Resume an existing capture after ingest, optionally with a tracked dashboard."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    run = args.run.resolve()
    from vitrine.construction import read_json
    status = read_json(run / 'construction-status.json') or {}
    if status.get('state') == 'running' and time.time() - status.get('heartbeat', 0) < 30:
        raise SystemExit('This capture is already running; refusing to start another pipeline.')
    if not (run / 'ingest/ingest.json').is_file():
        raise SystemExit('A completed ingest is required.')
    if (run / 'model/scene.ply').exists() or (run / 'archive').exists():
        raise SystemExit('Existing trained or packaged outputs found; choose stages explicitly.')
    if args.serve:
        from vitrine.serve import VitrineHandler, serve
        def start_capture(url, server):
            with (run / 'capture.log').open('a', encoding='utf-8') as log:
                child = subprocess.Popen([sys.executable, '-u', __file__, str(run)],
                                         stdout=log, stderr=log, stdin=subprocess.DEVNULL)
            VitrineHandler.capture_processes[run.name] = child
        serve(port=args.port, on_ready=start_capture)
        return
    from vitrine.cli import main as cli
    from vitrine.construction import Progress
    capture = json.loads((run / 'capture.json').read_text(encoding='utf-8'))
    common = ['--run-dir', str(run), '--quality', capture.get('quality', 'standard')]
    print('Resuming from prepared images; ingest is retained.', flush=True)
    for stage in ['sfm', 'train', 'evaluate', 'package']:
        command = common + [stage]
        if stage == 'package':
            command += ['--originals', str(run / 'source'), '--title', capture.get('title', run.name),
                        '--subject', capture.get('subject', 'Not recorded.')]
        with Progress(run, stage):
            result = cli(command)
            if result:
                raise RuntimeError(f'{stage} exited with code {result}; see capture.log')


if __name__ == '__main__':
    main()
