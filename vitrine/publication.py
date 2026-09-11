"""Recoverable publication for directory-based sidecar contracts.

Readers see the old or new directory, or a brief unavailable interval on Windows;
this is not a filesystem transaction. Old generations and failed staging remain.
Callers must hold their job lock throughout preparation and publication.
"""
from pathlib import Path
import os
import uuid


def publish_directory(staging: Path, destination: Path) -> Path | None:
    staging, destination = Path(staging), Path(destination)
    if staging.is_symlink() or destination.is_symlink():
        raise ValueError("Refusing symlinked publication directories")
    if not staging.is_dir() or staging.parent.resolve() != destination.parent.resolve():
        raise ValueError("Publication requires a sibling staging directory")
    previous = None
    if destination.exists():
        history = destination.with_name(destination.name + '-history')
        if history.is_symlink():
            raise ValueError("Refusing symlinked publication history")
        history.mkdir(exist_ok=True)
        previous = history / uuid.uuid4().hex
        os.replace(destination, previous)
    try:
        os.replace(staging, destination)
    except BaseException:
        if previous is not None and not destination.exists():
            os.replace(previous, destination)
        raise
    return previous
