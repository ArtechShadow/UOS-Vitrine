"""Configuration routing tests; no actual capture is processed."""
from vitrine.cli import build_parser, cmd_run


def test_custom_source_is_the_default_preservation_source(monkeypatch, tmp_path):
    captured = {}
    def run(args, stages):
        captured.update(vars(args))
        return 0
    monkeypatch.setattr('vitrine.pipeline.run_pipeline', run)
    args = build_parser().parse_args(['--run-dir', str(tmp_path/'run'), 'run', '--source', str(tmp_path/'Photos é')])
    assert cmd_run(args) == 0
    assert captured['originals'] == [str(tmp_path/'Photos é')]


def test_explicit_preservation_sources_are_retained(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr('vitrine.pipeline.run_pipeline', lambda args, stages: captured.update(vars(args)) or 0)
    args = build_parser().parse_args(['run', '--source', 'prepared', '--originals', 'raw'])
    assert cmd_run(args) == 0
    assert captured['originals'] == ['raw']


def test_viewer_export_precedes_optional_evaluation(monkeypatch):
    captured = []
    monkeypatch.setattr('vitrine.pipeline.run_pipeline', lambda args, stages: captured.extend(name for name, _ in stages) or 0)
    assert cmd_run(build_parser().parse_args(['run'])) == 0
    assert captured.index('train') < captured.index('export') < captured.index('evaluate')
