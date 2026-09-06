"""Launch the separately installed local SAM2 sidecar; never import its code."""
import argparse
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sidecar-root', required=True, type=Path)
    args, remaining = parser.parse_known_args()
    root = args.sidecar_root.resolve()
    executable = root / '.venv' / 'Scripts' / 'python.exe'
    if not executable.is_file():
        executable = root / '.venv' / 'bin' / 'python'
    runner = root / 'run_sam2_local.py'
    if not executable.is_file() or not runner.is_file():
        parser.error('The separate SAM2 sidecar environment and run_sam2_local.py must be installed first.')
    return subprocess.call([str(executable), str(runner), '--sidecar-root', str(root), *remaining], cwd=root)


if __name__ == '__main__':
    sys.exit(main())
