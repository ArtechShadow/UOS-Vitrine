# XR Lab Windows / RTX A6000 rehearsal

This runbook is for the release candidate on the Lab Windows workstation. The
status produced by the tooling is **candidate for A6000 rehearsal; visual
acceptance pending** until a person inspects the real result. The A6000 is the
`medium` efficiency target. `low` is the RTX 3060 Laptop and `high` is the RTX
5090; these labels identify a machine for matched efficiency tests and do not
change the reconstruction quality profile.

All drive paths below are illustrative placeholders, not observed workstation
locations. Replace them with your own paths; keep that local configuration out
of Git. External runner details describe the adapter interface only, not an
inspected private installation.

## 1. Check out safely

Keep the existing checkout untouched and create a detached worktree for the
candidate. This keeps the current checkout unchanged while the rehearsal uses
its own worktree:

```powershell
Set-Location 'D:\UOS-Vitrine'
git status --short --branch
git fetch origin codex/demo-ready-20260910
git worktree add --detach '..\UOS-Vitrine-A6000' origin/codex/demo-ready-20260910
Set-Location '..\UOS-Vitrine-A6000'
git status --short --branch
```

Use the candidate ref supplied by the maintainer if its name differs from
`codex/demo-ready-20260910`. If the candidate exists only as a local ref, use
`git worktree add --detach '..\UOS-Vitrine-A6000' codex/demo-ready-20260910`.
Do not reset, clean, force-push, or overwrite `codex/demo`.

## 2. Prepare an isolated Python environment

The preparation script creates `.venv-a6000` and does not modify an existing
environment. With networking available, use one command to create a fresh
Python 3.11 environment and install the project requirements into it:

```powershell
Set-Location '..\UOS-Vitrine-A6000'
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 -Mode Prepare -Install
```

`-Install` refuses an existing environment and never changes the selected
user environment. If the target environment was prepared separately, skip
`Prepare -Install` and point the observational commands at it:

```powershell
$envPath = 'D:\VitrineEnvs\a6000'
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 -Mode Check -EnvironmentPath $envPath -HardwareTarget medium
```

Do not use the 5090 constraints file for the A6000 unless its exact
Torch/CUDA compatibility has been separately verified. Required privileged
NVIDIA, Docker, or driver installation stays an explicit workstation step.

The observed-object Poisson route also needs its separate optional requirements
in this newly created environment (online preparation only):

```powershell
.venv-a6000\Scripts\python.exe -m pip install -r requirements-mesh.txt
```

These requirements use version lower bounds, not a fully locked Windows stack.
Record the resolved versions locally. Installation and native mesh execution
have not been tested on the A6000 host in this task.

## 3. Non-destructive prerequisite check

Run this after the driver, Python packages, Docker Desktop and model files are
available. It checks the actual GPU/driver/compute capability, synchronised
Torch CUDA execution, gsplat's extension, Docker's Linux engine and local
COLMAP image, ffmpeg/ffprobe, disk space, the requested port and optional
sidecar/mesh providers. No reconstruction starts and no weight is downloaded.

```powershell
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Check -EnvironmentPath .venv-a6000 -HardwareTarget medium -Port 8765
```

The check writes `runs/a6000-readiness.json` by default. A blocked result is
actionable: resolve the listed prerequisite and rerun it. A manual hardware
override can describe a machine for diagnostics, but it cannot pass the actual
GPU execution or A6000 acceptance gate.

## 4. Place real inputs and local model assets

Keep the source capture and model caches outside Git. Git does not transfer
`source/`, `runs/`, sidecar code, sidecar weights, or the CUDA/gsplat
extension cache. Preserve originals byte-for-byte and use a new output directory
for each attempt:

```text
D:\Captures\xr-lab-20260930\
├── stills\       photographs, retaining EXIF
├── video\        optional walkthrough video(s)
└── session.json  optional Vitrine Capture contract files
```

For the scene path, the core environment needs the project requirements, an
NVIDIA driver, Docker Desktop using Linux containers, the local
`colmap/colmap:latest` image, and a warmed gsplat extension built on the A6000
machine. Do not copy a Linux or RTX 5090 compiled extension cache to Windows or
to the A6000; warm it once in the target environment after the prerequisite
check.

For object isolation, configure the separately licensed local sidecar in its
own directory and set its executable and weight paths. For a local
image/mask mesh adapter, set `VITRINE_MESH_COMMAND_JSON` and
`VITRINE_MESH_WEIGHTS_JSON`. A configured executable or present GLB is not
evidence of correct identity or shape; the readiness report keeps that visual
gate pending.

## 5. Launch the normal dashboard

The launcher uses the existing Vitrine UI and saved hardware configuration. It
does not start a second server when used as the only launcher. `-WithObjects`
discovers the known local SAM2.1 runner when present, while a missing optional
sidecar leaves the scene dashboard available and visible as unconfigured.

```powershell
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Launch -EnvironmentPath .venv-a6000 -HardwareTarget medium `
  -HardwareConfig config\hardware.json -Port 8765 -OpenBrowser -WithObjects
```

To launch only one saved run in the dashboard, pass its run directory:

```powershell
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Launch -EnvironmentPath .venv-a6000 -RunDir runs\a6000-rehearsal-YYYYMMDD-HHMMSS
```

## 6. Run a fresh real-data rehearsal

`Rehearse` creates a timestamped run when `-RunDir` is omitted. It runs the
normal ingest → Docker COLMAP → training → export → evaluation → package
pipeline, then writes a readiness report even if a stage fails. It never
replaces another run. Use stills first for the shortest useful rehearsal and
add video when coverage requires it.

```powershell
$source = 'D:\Captures\xr-lab-20260930\stills'
$originals = 'D:\Captures\xr-lab-20260930'
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Rehearse -EnvironmentPath .venv-a6000 -HardwareTarget medium -Quality archive `
  -Source $source -Originals $originals `
  -RunDir 'D:\VitrineRuns\a6000-stills-20260910' `
  -Port 8765
```

Use `-Quality demo` for the shortest rehearsal. The `low`/`medium`/`high`
hardware labels identify the RTX 3060 Laptop, RTX A6000 and RTX 5090 for a
matched efficiency comparison; they do not alter the selected quality preset.

To include video, point `-Source` at a folder containing both `stills` and
`video`, preserving those subfolders. To rehearse a Vitrine Capture session,
use the existing CLI session option with the target environment after validating
the session:

```powershell
.venv-a6000\Scripts\python.exe -m vitrine capture-session validate `
  'D:\Captures\xr-lab-20260930\session.zip'
.venv-a6000\Scripts\python.exe -m vitrine `
  --run-dir 'D:\VitrineRuns\a6000-session-20260910' --quality demo run `
  --session 'D:\Captures\xr-lab-20260930\session.zip' `
  --originals 'D:\Captures\xr-lab-20260930'
```

The report is generated at the end of a failed run as well as a successful
one. Treat a process disappearance as failed/unknown and keep its logs and
checkpoint evidence.

## 7. Inspect, isolate and reconstruct an object

After the scene package exists, inspect the source views and the saved splat in
the dashboard. The dashboard's `-WithObjects` launch option configures the
separately installed sidecar. For a command-line retry, configure the same
sidecar contract in the current PowerShell session and use the normal
`objects` command; it stages and validates output under `<run>\objects`.
Then select exactly one object ID for the supported object-surface path; each
retry gets a new output generation.

```powershell
$python = (Resolve-Path .venv-a6000\Scripts\python.exe).Path
$run = (Resolve-Path D:\VitrineRuns\a6000-stills-20260910).Path
$originals = 'D:\Captures\xr-lab-20260930'
$external = (Resolve-Path tmp\external\vitrine-object-sidecar).Path
$runner = Join-Path $external 'run_sam2_local.py'
$env:VITRINE_OBJECT_SIDECAR = Join-Path $external '.venv\Scripts\python.exe'
$env:VITRINE_OBJECT_SIDECAR_ARGS_JSON = ConvertTo-Json -Compress -InputObject @(
  $runner, '--sidecar-root', $external,
  '--core-python', $python,
  '--core-importer', (Join-Path (Get-Location) 'scripts\import_sidecar_splats.py')
)

& $python -m vitrine --run-dir $run objects --timeout 1800
& $python -m vitrine --run-dir $run object-meshes --object-id obj_0001
& $python -m vitrine --run-dir $run package `
  --originals $originals `
  --title 'XR Lab A6000 rehearsal' `
  --subject 'Real captured installation space'
& $python -m vitrine verify (Join-Path $run 'archive')
```

Use the dashboard's object card and mesh viewer to inspect the selected output.
The sidecar's source crop is evidence of the mask input; it is not a 3D render.
If support is insufficient, retain the diagnostics and report the object as
unreconstructed rather than filling unseen surfaces.

## 8. Retry failed postprocessing and recover

The scene master and archive are independent of optional mesh failures. Check
the recorded state before retrying and use a new object output generation. A
failed run can resume only when its saved input/configuration fingerprints are
compatible:

```powershell
$python = '.venv-a6000\Scripts\python.exe'
$source = 'D:\Captures\xr-lab-20260930\stills'
$originals = 'D:\Captures\xr-lab-20260930'
$run = 'D:\VitrineRuns\a6000-stills-20260910'

# Inspect state and logs first.
Get-Content (Join-Path $run 'pipeline.json')
Get-Content (Join-Path $run 'logs\vitrine.log') -Tail 80

# Resume the same recorded recipe and explicit source/original paths; this
# does not restore optimiser state from a partial training process.
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Rehearse -Resume -EnvironmentPath .venv-a6000 -Quality archive `
  -Source $source -Originals $originals -RunDir $run

# Retry only object meshing after fixing the local sidecar/mesh prerequisites.
& $python -m vitrine --run-dir $run object-meshes --object-id obj_0001
& $python -m vitrine --run-dir $run package --originals $originals
& $python -m vitrine verify (Join-Path $run 'archive')

# Regenerate the machine-readable gate and compare hashes with the prior report.
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Readiness -EnvironmentPath .venv-a6000 -HardwareTarget medium `
  -RunDir $run -Source $source -Offline
```

## 9. Offline rehearsal

Complete package installation, model placement, Docker image pull and gsplat
warm-up before disconnecting networking. Then run the check with `-Offline`,
start the dashboard with the same flag, and confirm the report records local
assets/caches. The core launcher sets `VITRINE_OFFLINE` and
`HF_HUB_OFFLINE`; a separately installed sidecar or mesh provider must honor
those flags. The readiness report keeps external offline execution pending
until its logs and network behavior have been observed.

```powershell
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Check -EnvironmentPath .venv-a6000 -HardwareTarget medium `
  -RunDir 'D:\VitrineRuns\a6000-stills-20260910' -Offline
powershell -NoProfile -File scripts\a6000_rehearsal.ps1 `
  -Mode Launch -EnvironmentPath .venv-a6000 -HardwareTarget medium `
  -RunDir 'D:\VitrineRuns\a6000-stills-20260910' -Offline -OpenBrowser
```

## 10. Matched efficiency comparison

For low/medium/high measurements, keep the source fingerprint, selected frame
set, COLMAP image/backend, profile, crop, resolution, iteration count, seed,
held-out split and output format identical. Record total wall time, each stage
time, peak GPU memory, Gaussian count, PSNR/SSIM and archive/object outcomes in
the generated reports. An A6000 capability override or a 5090 historical ETA is
not a measurement for another machine.

## 11. Return to the previous known-good branch

Stop the dashboard after the run has finished, preserve the new run and report,
then return without deleting evidence:

```powershell
Set-Location 'D:\UOS-Vitrine'
git status --short --branch
# The original checkout was never switched; use its recorded branch as-is.
git branch --show-current
# If it was codex/demo and you need to work there explicitly:
git switch codex/demo
# The candidate worktree can remain for inspection until its run is archived.
```

## Visual acceptance checklist

Record the human review only after all of these are inspected from the real
capture:

- The selected object identity matches the source photographs and its mask
  aligns with the same frame/instance.
- The room views are coherent from captured viewpoints, with no giant sheets,
  elongated outliers, false bridges or mixed components.
- The object mesh has a credible shape from several captured viewpoints;
  unseen or occluded surfaces are visibly disclosed rather than invented.
- The GLB reopens with correct orientation, scale, framing and embedded buffers.
- The preservation archive verifies after export, and its artefact hashes match
  the report being reviewed.

Only then may a human explicitly record approval with the readiness tool. Any
changed model, mesh, export or archive artefact invalidates the recorded
approval and returns it to pending.


Cancellation limitation: after a sidecar timeout, inspect attempt-owned child
processes before retrying; automatic descendant termination is not implemented.
Stop the dashboard before an occupied-port readiness check if it is already
using the selected port. Never kill unrelated Python or GPU processes.
