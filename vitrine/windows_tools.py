"""Find installed CLI tools when a desktop parent has an older PATH."""
import os
import shutil
from pathlib import Path


def configure():
    if os.name != 'nt':
        return
    root = Path(__file__).resolve().parent.parent
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
    program_files = Path(os.environ.get('ProgramFiles', 'C:/Program Files'))
    candidates = {
        'docker.exe': [local / 'Programs/DockerDesktop/resources/bin',
                       program_files / 'Docker/Docker/resources/bin'],
        'ffmpeg.exe': sorted((root / 'output/setup/ffmpeg').glob('*/bin'), reverse=True),
    }
    for executable, folders in candidates.items():
        if shutil.which(executable):
            continue
        for folder in folders:
            if (folder / executable).is_file():
                os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')
                break


def docker_engine_ready():
    import subprocess
    executable = shutil.which('docker')
    if not executable:
        return False
    try:
        result = subprocess.run([executable, 'info', '--format', '{{.OSType}}'],
                                capture_output=True, text=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode == 0 and result.stdout.strip() == 'linux'
    except (OSError, subprocess.TimeoutExpired):
        return False
