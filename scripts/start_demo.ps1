param(
    [int]$Port = 8765,
    [string[]]$Run = @(),
    [switch]$WithObjects
)

$ErrorActionPreference = 'Stop'
$project = Split-Path $PSScriptRoot -Parent
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the project Python environment first.' }
$previousExecutable = $env:VITRINE_OBJECT_SIDECAR
$previousArguments = $env:VITRINE_OBJECT_SIDECAR_ARGS_JSON
try {
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
    $arguments = @('-m', 'vitrine', 'ui', '--port', "$Port")
    foreach ($name in $Run) { $arguments += @('--only', $name) }
    Push-Location $project
    try { & $python @arguments }
    finally { Pop-Location }
} finally {
    $env:VITRINE_OBJECT_SIDECAR = $previousExecutable
    $env:VITRINE_OBJECT_SIDECAR_ARGS_JSON = $previousArguments
}
