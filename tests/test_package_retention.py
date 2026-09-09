"""Software preservation invariants using byte fixtures, not media quality."""
import pytest
from vitrine.package import _copy_tree


def test_conflicting_original_names_are_not_silently_overwritten(tmp_path):
    a, b, output = tmp_path/'a', tmp_path/'b', tmp_path/'archive'
    a.mkdir(); b.mkdir()
    (a/'same.jpg').write_bytes(b'camera a')
    (b/'same.jpg').write_bytes(b'camera b')
    _copy_tree(a, output, description='first source')
    with pytest.raises(ValueError, match='Conflicting preservation'):
        _copy_tree(b, output, description='second source')
    assert (output/'same.jpg').read_bytes() == b'camera a'
    assert (b/'same.jpg').read_bytes() == b'camera b'


def test_archive_rejects_symlinked_parent(tmp_path):
    import json
    from vitrine.package import sha256, verify_package
    outside = tmp_path / 'outside'
    outside.mkdir()
    payload = outside / 'source.jpg'
    payload.write_bytes(b'original')
    archive = tmp_path / 'archive'
    archive.mkdir()
    try:
        (archive / 'originals').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable on this host')
    (archive / 'manifest.json').write_text(json.dumps({
        'files': [{'path': 'originals/source.jpg', 'bytes': 8,
                   'sha256': sha256(payload)}],
        'file_count': 1, 'total_bytes': 8,
    }))
    ok, problems = verify_package(archive)
    assert not ok
    assert any('UNSAFE SYMLINK' in p for p in problems)
