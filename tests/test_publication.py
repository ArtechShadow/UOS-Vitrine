"""Injected filesystem failures test retention, never reconstruction quality."""
from unittest.mock import patch
import os
import pytest
from vitrine.publication import publish_directory


def test_failed_replace_restores_prior_and_keeps_attempt(tmp_path):
    old, new = tmp_path/'objects', tmp_path/'.staging'
    old.mkdir(); new.mkdir()
    (old/'asset').write_bytes(b'accepted')
    (new/'asset').write_bytes(b'candidate')
    replace = os.replace
    def fail_source(source, target):
        if source == new:
            raise OSError('injected publication failure')
        return replace(source, target)
    with patch('vitrine.publication.os.replace', side_effect=fail_source):
        with pytest.raises(OSError, match='injected'):
            publish_directory(new, old)
    assert (old/'asset').read_bytes() == b'accepted'
    assert (new/'asset').read_bytes() == b'candidate'


def test_previous_generation_is_retained(tmp_path):
    old, new = tmp_path/'objects', tmp_path/'.staging'
    old.mkdir(); new.mkdir()
    (old/'asset').write_bytes(b'accepted')
    (new/'asset').write_bytes(b'candidate')
    previous = publish_directory(new, old)
    assert (previous/'asset').read_bytes() == b'accepted'
    assert (old/'asset').read_bytes() == b'candidate'
