param(
    [int]$Port = 8765,
    [string[]]$Run = @(),
    [switch]$WithObjects,
    [switch]$OpenBrowser,
    [switch]$Offline,
    [switch]$Check,
    [Alias('Python')]
    [string]$PythonPath = '',
    [Alias('ConfigPath')]
    [string]$HardwareConfig = '',
    [string]$RunsRoot = '',
    [string]$ReadinessReport = '',
    [ValidateSet('low', 'medium', 'high')]
    [string]$HardwareTarget = 'medium'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $project '.venv\Scripts\python.exe'
} else {
    $PythonPath
}
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw "Python environment not found: $python" }

$previousExecutable = $env:VITRINE_OBJECT_SIDECAR
$previousArguments = $env:VITRINE_OBJECT_SIDECAR_ARGS_JSON
$previousHardwareConfig = $env:VITRINE_HARDWARE_CONFIG
$previousOffline = $env:VITRINE_OFFLINE
$previousHfOffline = $env:HF_HUB_OFFLINE
try {
    if (-not [string]::IsNullOrWhiteSpace($HardwareConfig)) {
        $config = if ([IO.Path]::IsPathRooted($HardwareConfig)) {
            $HardwareConfig
        } else {
            Join-Path $project $HardwareConfig
        }
        if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
            throw "Hardware config was not found: $config"
        }
        $env:VITRINE_HARDWARE_CONFIG = (Resolve-Path -LiteralPath $config).Path
    }
    if ($Offline) {
        # The core pipeline does not download anything.  This marker is also
        # inherited by a separately installed sidecar so its adapter can
        # enforce offline model loading rather than attempting a surprise pull.
        $env:VITRINE_OFFLINE = '1'
        $env:HF_HUB_OFFLINE = '1'
    }

    if ($WithObjects) {
        $external = Join-Path $project 'tmp\external\vitrine-object-sidecar'
        $sidecarPython = Join-Path $external '.venv\Scripts\python.exe'
        $runner = Join-Path $external 'run_sam2_local.py'
        # An object separator is an optional, separate process. Respect an
        # explicit environment configuration first; the core scene dashboard
        # must still open when the experimental sidecar is absent.
        if ([string]::IsNullOrWhiteSpace($env:VITRINE_OBJECT_SIDECAR) -and
            (Test-Path -LiteralPath $sidecarPython) -and (Test-Path -LiteralPath $runner)) {
            $env:VITRINE_OBJECT_SIDECAR = $sidecarPython
        }
        if ([string]::IsNullOrWhiteSpace($env:VITRINE_OBJECT_SIDECAR)) {
            Write-Warning 'Optional object sidecar is not installed; continuing with the scene dashboard.'
        } elseif ([string]::IsNullOrWhiteSpace($env:VITRINE_OBJECT_SIDECAR_ARGS_JSON) -and
                  (Test-Path -LiteralPath $runner)) {
            # Keep runner defaults and caller-provided settings. In particular,
            # do not silently constrain frame count or prompts for a venue run.
            $env:VITRINE_OBJECT_SIDECAR_ARGS_JSON = ConvertTo-Json -Compress -InputObject @(
                $runner, '--sidecar-root', $external,
                '--core-python', $python,
                '--core-importer', (Join-Path $PSScriptRoot 'import_sidecar_splats.py')
            )
        } elseif (-not [string]::IsNullOrWhiteSpace($env:VITRINE_OBJECT_SIDECAR_ARGS_JSON)) {
            Write-Host 'Using VITRINE_OBJECT_SIDECAR_ARGS_JSON from the environment.'
        }
    }

    if ($Check -or -not [string]::IsNullOrWhiteSpace($ReadinessReport)) {
        $readiness = Join-Path $PSScriptRoot 'rehearsal_readiness.py'
        $readinessArgs = @(
            $readiness,
            '--project-root', $project,
            '--python', $python,
            '--hardware-target', $HardwareTarget,
            '--port', "$Port"
        )
        if (-not [string]::IsNullOrWhiteSpace($ReadinessReport)) {
            $report = if ([IO.Path]::IsPathRooted($ReadinessReport)) {
                $ReadinessReport
            } else {
                Join-Path $project $ReadinessReport
            }
            $readinessArgs += @('--output', $report)
        }
        if ($Offline) { $readinessArgs += '--offline' }
        & $python @readinessArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Readiness checks failed; see the generated report before launching the dashboard."
        }
        if ($Check) { return }
    }

    $arguments = @('-m', 'vitrine', 'ui', '--port', "$Port")
    if (-not [string]::IsNullOrWhiteSpace($RunsRoot)) {
        $runsRootPath = if ([IO.Path]::IsPathRooted($RunsRoot)) {
            $RunsRoot
        } else {
            Join-Path $project $RunsRoot
        }
        $arguments += @('--runs-root', $runsRootPath)
    }
    if ($OpenBrowser) { $arguments += '--open' }
    foreach ($name in $Run) { $arguments += @('--only', $name) }
    Push-Location $project
    try { & $python @arguments }
    finally { Pop-Location }
} finally {
    $env:VITRINE_OBJECT_SIDECAR = $previousExecutable
    $env:VITRINE_OBJECT_SIDECAR_ARGS_JSON = $previousArguments
    $env:VITRINE_HARDWARE_CONFIG = $previousHardwareConfig
    $env:VITRINE_OFFLINE = $previousOffline
    $env:HF_HUB_OFFLINE = $previousHfOffline
}
