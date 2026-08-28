"""The ``objects`` subcommand: subprocess-only, explicit args, validated output."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vitrine.cli import build_parser, cmd_objects


def _args(run_dir: Path, sidecar=None, sidecar_arg=None) -> argparse.Namespace:
    return argparse.Namespace(run_dir=str(run_dir), sidecar=sidecar, sidecar_arg=sidecar_arg)


def _write_valid(out_dir: Path) -> None:
    """Write a valid objects tree into ``out_dir``, as a good sidecar would."""
    (out_dir / "obj_001").mkdir(parents=True)
    mesh = out_dir / "obj_001" / "mesh.glb"
    mesh.write_bytes(b"glb")
    doc = {
        "schema": "vitrine/object/1",
        "objects": [{
            "object_id": "obj_001", "label": "radio", "mesh_path": "obj_001/mesh.glb",
            "sha256": hashlib.sha256(b"glb").hexdigest(),
        }],
    }
    (out_dir / "objects.json").write_text(json.dumps(doc), encoding="utf-8")


def _fake_sidecar(command, *a, **k):
    """Stand-in sidecar: write valid output into the --out staging directory."""
    out = Path(command[command.index("--out") + 1])
    _write_valid(out)
    return SimpleNamespace(returncode=0)


def test_objects_subcommand_is_registered():
    # leading-dash values need the =form, standard argparse behaviour
    args = build_parser().parse_args(
        ["objects", "--sidecar", "run-me", "--sidecar-arg=-m", "--sidecar-arg", "sidecar"]
    )
    assert args.command == "objects" and args.func is cmd_objects
    assert args.sidecar == "run-me" and args.sidecar_arg == ["-m", "sidecar"]


def test_no_sidecar_configured_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    assert cmd_objects(_args(tmp_path)) == 2
    assert "no object sidecar" in capsys.readouterr().err


def test_success_invokes_subprocess_and_validates_output(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    with mock.patch("subprocess.run", side_effect=_fake_sidecar) as run:
        code = cmd_objects(_args(tmp_path, sidecar="python", sidecar_arg=["-m", "sidecar"]))
    assert code == 0
    # explicit executable + args, no shell parsing; sidecar runs into staging
    command = run.call_args.args[0]
    assert command[:3] == ["python", "-m", "sidecar"]
    assert "--package" in command and str(tmp_path) in command
    assert "--out" in command and str(tmp_path / ".objects.staging") in command
    # published atomically to objects/, staging cleaned up
    assert (tmp_path / "objects" / "objects.json").is_file()
    assert not (tmp_path / ".objects.staging").exists()


def test_publish_replaces_stale_prior_output(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    stale = tmp_path / "objects"
    stale.mkdir()
    (stale / "stale.txt").write_text("old", encoding="utf-8")
    with mock.patch("subprocess.run", side_effect=_fake_sidecar):
        assert cmd_objects(_args(tmp_path, sidecar="run")) == 0
    # stale file from the prior invocation is gone after atomic publish
    assert not (tmp_path / "objects" / "stale.txt").exists()
    assert (tmp_path / "objects" / "obj_001" / "mesh.glb").is_file()


def test_windows_style_path_with_spaces_is_one_arg(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    exe = r"C:\Program Files\sidecar\run.exe"
    with mock.patch("subprocess.run", side_effect=_fake_sidecar) as run:
        assert cmd_objects(_args(tmp_path, sidecar=exe)) == 0
    # the space- and backslash-bearing path stays a single argv entry
    assert run.call_args.args[0][0] == exe


def test_env_var_supplies_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("VITRINE_OBJECT_SIDECAR", "sidecar-bin")
    with mock.patch("subprocess.run", side_effect=_fake_sidecar) as run:
        assert cmd_objects(_args(tmp_path)) == 0
    assert run.call_args.args[0][0] == "sidecar-bin"


def test_invalid_output_leaves_prior_output_intact(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    good = tmp_path / "objects"
    good.mkdir()
    (good / "keep.txt").write_text("prior", encoding="utf-8")
    # sidecar exits 0 but writes nothing valid into staging -> invalid -> exit 3
    with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)):
        assert cmd_objects(_args(tmp_path, sidecar="quiet")) == 3
    # the good prior output is untouched, staging cleaned up
    assert (good / "keep.txt").read_text() == "prior"
    assert not (tmp_path / ".objects.staging").exists()


def test_zero_exit_with_invalid_output_is_failure(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    # sidecar exits 0 but writes nothing -> invalid contract -> exit 3
    with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)):
        assert cmd_objects(_args(tmp_path, sidecar="quiet")) == 3


def test_sidecar_failure_maps_to_stable_code(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=42)):
        assert cmd_objects(_args(tmp_path, sidecar="boom")) == 5   # not the raw 42


def test_missing_sidecar_binary_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    with mock.patch("subprocess.run", side_effect=OSError("no such file")):
        assert cmd_objects(_args(tmp_path, sidecar="nope")) == 4
