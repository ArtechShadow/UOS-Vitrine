# Demo recovery report

**Status: candidate for A6000 rehearsal; visual acceptance pending.**
The software candidate does not yet establish a working high-fidelity real-data
object reconstruction: the external evidence contract and GPU acceptance remain blocked.

## Source and capabilities

Starting branch/commit: `codex/demo` at
`2eaed9378cac2086a2c0411503f468c6a2b9f614`, confirmed by `git ls-remote` and a
fresh clone. Recovery branch: `codex/demo-ready-20260910`. No unrelated working
tree existed in this runtime. A detached baseline worktree preserves the exact
starting tree. No reset, clean, force push, automatic merge, or edits to main
or codex/demo have been performed.

The actual runtime is Linux x86_64 with Python 3.12.14, approximately 21 GiB
RAM and 30 GiB initially free disk. ffmpeg is present. NVIDIA/GPU, Docker,
native COLMAP and PowerShell executables are absent. CPU Torch 2.14.0+cpu was
installed into an isolated scratch target for software tests, together with
pytest 9.1.1, plyfile 1.1.5, NumPy 2.5.3 and OpenCV headless 5.0.0.93. These
are **test host versions, not the validated Windows dependency recipe**.
Project ML requirements, drivers and measured hardware presets are unchanged.

GitHub connector reads succeed; unauthenticated Git fetch/clone succeeds.
Shell push failed: `could not read Username for https://github.com: terminal
prompts disabled`. Publication uses the GitHub connector because shell Git has no push credentials.
The PR records the exact tested candidate commit; local milestones retain the
implementation history. Neither publication nor software tests imply approval.

Private evidence is **not present/inspected**: `source/xr-lab-20260909/`,
`runs/xr-lab-20260909-hq/`,
`report/xr-lab-20260909-rehearsal/README.md`, the corresponding `report.html`,
segmentation-refinement, objects, object-meshes, attempts and evidence. The
installed external `run_sam2_local.py`, SAM2/GroundingDINO weights and encoder
cache are not present. Git does not transfer them. Historical model hashes,
quality measurements and rejection screenshots are documentation, not new
observations from this task.

## Confirmed defects versus hypotheses

Confirmed by reading the starting code:

- CLI sidecar publication deleted previous outputs and discarded failed staging.
- Still staging mapped same-stem files to the same JPEG and retained EXIF
  orientation without normalising its pixels before calibration.
- Basename fallback could silently choose one of multiple registered images.
- FULL_OPENCV rectification omitted its rational denominator; unsupported camera
  models could pass through as though suitable for pinhole rendering.
- A custom `run --source` could package the unrelated default `source/` folder.
- Multiple archive input roots could overwrite the same destination filename.
- Object meshing selected scene-wide views, used `trim_fraction=0` and could
  reconstruct a lossy viewing derivative when full-SH evidence was missing.

The 30,000-step historical process disappearance has **no established cause**.
OOM, evaluation failure and process termination remain hypotheses. This task
has no original process logs or GPU reproduction to distinguish them.
The historical same-frame external seed association bug cannot be rechecked
against the absent installed runner. In-repo contracts can require exact
instance/mask evidence; they cannot repair uninspected sibling code.

## Implementation and software validation

Four bounded workers use the requested available `gpt-5.6-luna` / `max`
settings without substitution. Ownership: object support/mesher/importer;
trainer/save/recovery; dashboard/browser; A6000/preflight/rehearsal tooling.
The coordinator owns CLI, calibration, ingest, archive copying, integration
review and final reporting. External code remains in separate processes.

The baseline suite at the original SHA is **113 passed, 5 skipped (2.05 s)**.
Three skips need a real running capture dashboard; two need private capture
media/Polycam geometry. Initial test invocations failed because pytest and then
Torch were absent; isolated test dependencies resolved those collection limits.

Coordinator focused checks: calibration/publication/CLI 17 passed; ingest
7 passed; source-package routing 2 passed; archive retention/package 10 passed;
sidecar CLI including timeout 11 passed. Counts overlap and are not a sum of
unique tests. Deterministic fixtures and injected failures test software
contracts only. No PSNR improvement, reconstruction quality, A6000 timing,
real SAM2 execution or visual acceptance is claimed.

## Commands executed

From a fresh task workspace, then repository root unless stated otherwise:

```bash
git ls-remote https://github.com/ArtechShadow/UOS-Vitrine.git refs/heads/codex/demo
git clone --branch codex/demo --single-branch https://github.com/ArtechShadow/UOS-Vitrine.git UOS-Vitrine
git status --short
git remote -v
git log -3 --oneline
rg --files -g AGENTS.md
git switch -c codex/demo-ready-20260910
git worktree add --detach ../UOS-Vitrine-baseline 2eaed9378cac2086a2c0411503f468c6a2b9f614
uname -a
python --version
df -h .
free -h
command -v nvidia-smi ffmpeg colmap docker pwsh node
python -m pytest -q
python -m pip install --target ../test-deps pytest plyfile scipy opencv-python-headless
python -m pip install --target ../torch-cpu --index-url https://download.pytorch.org/whl/cpu torch
# Run in the detached baseline worktree:
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q -ra
# Focused checks in the recovery tree:
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q tests/test_camera_conventions.py tests/test_publication.py tests/test_cli_objects.py
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q tests/test_ingest.py
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q tests/test_source_package.py
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q tests/test_package_retention.py tests/test_package_objects.py
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q tests/test_cli_objects.py
git diff --cached --check
GIT_TERMINAL_PROMPT=0 git push -u origin codex/demo-ready-20260910
```

README, AGENTS and the seven requested failure/setup/capture documents were
read, followed by relevant current modules/tests. Commands use relative paths
here to remain portable; scratch paths contain no institutional media.

## Integrated repairs

- Object support now requires exact COLMAP image/camera IDs, instance/mask
  identities and hashes, explicit raw/rectified calibration, verified full-SH
  source and scene lineage. Missing/stale evidence fails closed.
- Object views require angular diversity and mask-consistent observation.
  Fusion checks opacity, camera-Z depth, full-scene occlusion and multi-view
  agreement. Mesh vertices and triangle interiors must retain observed support.
  Non-finite geometry and contaminated bounds are diagnosed; no blanket
  compactness deletion silently removes thin parts. Selected IDs run independently.
- The retained Poisson route is explicitly experimental. No TSDF backend was
  added. GPU depth rendering is followed by CPU meshing; this is not an entirely
  GPU implementation. Vertex-colour GLB is not a certified high-fidelity textured
  asset. Normal orientation and real surface fidelity remain unverified.
- Chunked atomic full-SH saves reopen and verify geometry, coefficients, counts
  and hashes before promotion. Last-good masters survive injected failures.
  Master save, viewer export, evaluation and packaging have distinct states.
  Viewer export precedes optional evaluation in the normal pipeline.
- Resume fingerprints include input content, configuration and relevant modules.
  Incompatible stages require a fresh run; restart is not optimiser-state resume.
- Dashboard exposes exact per-view evidence, selected object meshing, retained
  failures, cancellation and unknown/disappeared worker states. Refresh does not
  start duplicate workers. Existing branding and views remain.
- Archive source routing/copy verification preserves originals and rejects
  collisions and symlink traversal. Sidecar/mesh publication retains previous
  generations and failed evidence. Directory replacement has a brief unavailable
  interval on Windows; it is not claimed to be a filesystem transaction.
- PowerShell extends the existing launcher with isolated Python 3.11 preparation,
  checks, configurable rehearsal and hash-bound readiness. Low/Medium/High mean
  RTX 3060 Laptop / RTX A6000 / RTX 5090 and do not alter quality settings.

Independent cross-review found and prompted repairs to stale saved-state flags,
clean-exit false success, missing full-SH checks, unsupported triangle interiors,
archive symlink parents. Subprocess-tree termination remains a limitation. Tests use deterministic
software fixtures and injected failures, not fabricated capture evidence.

Static HTTP smoke succeeded for the dashboard, JS, viewer, health and runs API.
JavaScript syntax checks passed. Playwright browser launch was blocked because
Chromium is not installed; browser console, WebGL and intercepted offline
request checks are NOT RUN. External sidecar offline behaviour is unverified.

## Remaining gates

BLOCKED here: fresh COLMAP, CUDA training/final save, real SAM2 isolation,
observed object surface quality, Windows PowerShell and A6000 execution.
Offline real capture workflow and human approval remain NOT RUN/PENDING.
Whole-room meshing is separate and NOT RUN; it is not part of this repair's
critical path. See `demo-recovery-progress.md` for the next exact command.


The exact external runner requirement is in [object-support-contract.md](object-support-contract.md).
Existing outputs without that evidence can be inspected but cannot satisfy the
object mesh gate. The absent runner must be updated separately without crossing
licence/process boundaries. No paid/cloud API or generative completion is used.

Known residual limitations: HDR/16-bit conversion tone-map provenance is not
fully implemented; Windows case-colliding original archive names need a real
cross-platform transfer check. The generated [readiness report](demo-recovery-readiness.json)
contains no real artifact hashes because no permitted capture exists here.
No throughput, PSNR improvement, GPU memory peak or visual quality was measured.

## Final integration verification record

An intermediate integration run reported 172 passed, 5 skipped and one failure
in the new readiness status-normalization regression. It exposed a legacy
`error`/`optional` versus `fail`/`required` contract mismatch; the producer/consumer
boundary was repaired without weakening the test. Earlier integration also
caught a missing import and incomplete validation; those were repaired before
publication. The final integrated result is recorded below.

Additional executed checks:

```bash
PYTHONPATH=../lint-deps python -m ruff check --select E9,F63,F7,F82 vitrine scripts tests
python -m compileall -q vitrine scripts tests
node --check vitrine/ui/app.js
node --check vitrine/ui/studio.js
git diff --check
PYTHONPATH=../test-deps:../torch-cpu python -m pytest -q -ra
PYTHONPATH=../test-deps:../torch-cpu python scripts/rehearsal_readiness.py --hardware-target medium --output docs/demo-recovery-readiness.json --port 0
```

Readiness generation deliberately exits 1 on this blocked CPU host. Port 0
skips the occupied-port probe for the saved public report; the Lab runbook uses
port 8765. This is a capability report, not a passed real-capture rehearsal.


Final integrated software source commit: `6ac5df3f8e5995fe91553e6eb54cb3186a8ff192`.
**178 passed, 5 skipped in 3.73 seconds**. Skips: three tests require
`VITRINE_DEMO_URL` with real capture results; two require `VITRINE_REAL_ROOT`
with private capture/Polycam evidence. No failing tests remain. Compilation,
JavaScript syntax, Ruff fatal-error checks and `git diff --check` passed.
The final documentation/readiness publication commit is identified by the PR;
its tree is compared byte-for-byte with this local recovery tree before release.

The generated readiness record is correctly BLOCKED: software dependencies,
CUDA/target hardware, real reconstruction and offline acceptance are missing.
It records zero artifact hashes and human approval pending. All four workers
finished and their changes were integrated before the final suite.

**Remaining cancellation limitation:** CLI sidecar timeout terminates the direct
child through `subprocess.run`; descendant processes are not yet guaranteed to
terminate. Cooperative pipeline cancellation can wait for a blocking external
stage. Before retrying after timeout/cancellation on the Lab, inspect and stop
only the processes belonging to that attempt. Automatic process-tree cleanup
needs a follow-up Windows-tested change; no blanket process kill is supplied.

The public branch is a candidate for rehearsal, not a statement that all image/
video inputs now produce high-fidelity meshes. Required external evidence,
Windows execution and human inspection still determine whether it is acceptable.


Publication review rejected the first documentation tree because an existing
setup document still contained personal paths and private installation details.
The public setup guide was replaced with a generic interface/runbook reference,
and readiness now emits only the interpreter filename. Historical Git commits
remain untouched; private installation inventories and measurements are not
republished in the changed guide. No approval or security policy was bypassed.


Published implementation commit: `1754d777516e53299b3774d7822730648a2c0113`.
Draft PR: https://github.com/ArtechShadow/UOS-Vitrine/pull/8 targeting `codex/demo`.
A fresh detached checkout of that exact commit passed **178 tests, 5 skipped
in 3.77 seconds**, plus compile, JavaScript syntax, Ruff and diff checks.
The subsequent documentation-only handover update uses FETCH_HEAD so the safe
checkout commands also work with single-branch clone refspecs. The PR identifies
the final documentation head. No Lab transfer, installation or approval occurred.
