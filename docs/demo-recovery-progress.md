# Recovery handover — integrated software milestone

Status: candidate for A6000 rehearsal; visual acceptance pending.
Base: `codex/demo` at `2eaed9378cac2086a2c0411503f468c6a2b9f614`.
Recovery branch: `codex/demo-ready-20260910`; original branch untouched.

Completed: calibrated object-support contracts and strict surface rejection;
full-SH save/recovery; dashboard selected-object/evidence/recovery flow;
ingest/archive preservation; Windows check/prepare/launch/rehearsal tooling.
Four Luna/Max workers completed owned milestones and cross-reviewed integration.
See `demo-recovery-report.md` for tests, capabilities and confirmed limitations.

This Linux CPU host has no GPU, Docker/COLMAP, PowerShell, private capture,
installed external SAM2 runner or weights. No A6000, 5090 or 3060 run occurred.
The historical 30k process disappearance still has no established cause.

Next software command from the repository root in this task runtime:

```bash
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q -ra
```

Test-only dependency folders are not included in Git. On the Lab, start with
an isolated worktree using `docs/a6000-rehearsal.md`, then the exact first check:

```powershell
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 -Mode Check -EnvironmentPath .venv-a6000 -HardwareTarget medium
```

If the environment is absent, follow the documented one-time online isolated
Prepare -Install step first. Transfer original captures, model files and the
separately licensed runner outside Git. Verify the runner supplies the exact
`object-support-contract.md` evidence before object reconstruction. Use a new run
directory, retain diagnostics on insufficient support, inspect real masks and
surfaces, reopen GLB, verify archive, then record human approval against hashes.
Whole-room meshing is separate. Do not merge or call the demo ready based on tests.

Final integrated CPU software suite: **178 passed, 5 skipped** at source commit
`6ac5df3f8e5995fe91553e6eb54cb3186a8ff192`. Readiness is BLOCKED as expected.
Sidecar descendant termination after timeout remains a known follow-up; inspect
attempt-owned processes before retrying. See PR for published commit/tree.


Published implementation commit: `1754d777516e53299b3774d7822730648a2c0113`.
Draft PR: https://github.com/ArtechShadow/UOS-Vitrine/pull/8 targeting `codex/demo`.
A fresh detached checkout of that exact commit passed **178 tests, 5 skipped
in 3.77 seconds**, plus compile, JavaScript syntax, Ruff and diff checks.
The subsequent documentation-only handover update uses FETCH_HEAD so the safe
checkout commands also work with single-branch clone refspecs. The PR identifies
the final documentation head. No Lab transfer, installation or approval occurred.
