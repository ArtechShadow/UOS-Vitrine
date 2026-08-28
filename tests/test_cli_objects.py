"""The ``objects`` subcommand invokes the sidecar as a subprocess, never imports it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from vitrine.cli import build_parser, cmd_objects


def _args(run_dir: Path, sidecar=None) -> argparse.Namespace:
    return argparse.Namespace(run_dir=str(run_dir), sidecar=sidecar)


def test_objects_subcommand_is_registered():
    args = build_parser().parse_args(["objects", "--sidecar", "run-me"])
    assert args.command == "objects"
    assert args.func is cmd_objects
    assert args.sidecar == "run-me"


def test_no_sidecar_configured_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    assert cmd_objects(_args(tmp_path)) == 2
    assert "no object sidecar" in capsys.readouterr().err


def test_invokes_subprocess_with_run_and_out(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    out = tmp_path / "objects"
    out.mkdir()
    (out / "objects.json").write_text(
        json.dumps({"objects": [{"label": "radio"}, {"label": "sofa"}]}), encoding="utf-8"
    )
    with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
        code = cmd_objects(_args(tmp_path, sidecar="python -m sidecar"))
    assert code == 0
    command = run.call_args.args[0]
    assert command[:3] == ["python", "-m", "sidecar"]
    assert "--package" in command and str(tmp_path) in command
    assert "--out" in command and str(out) in command


def test_env_var_supplies_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("VITRINE_OBJECT_SIDECAR", "sidecar-bin")
    with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=0)) as run:
        cmd_objects(_args(tmp_path))
    assert run.call_args.args[0][0] == "sidecar-bin"


def test_sidecar_failure_propagates_exit_code(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    with mock.patch("subprocess.run", return_value=SimpleNamespace(returncode=3)):
        assert cmd_objects(_args(tmp_path, sidecar="boom")) == 3


def test_missing_sidecar_binary_is_reported(tmp_path, monkeypatch):
    monkeypatch.delenv("VITRINE_OBJECT_SIDECAR", raising=False)
    with mock.patch("subprocess.run", side_effect=OSError("no such file")):
        assert cmd_objects(_args(tmp_path, sidecar="nope")) == 1
