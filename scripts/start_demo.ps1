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
        if (-not (Test-Path -LiteralPath $sidecarPython) -or -not (Test-Path -LiteralPath $runner)) {
            throw 'The separate SAM2 sidecar is not installed. See docs/demo-object-sidecar-review.md.'
        }
        $env:VITRINE_OBJECT_SIDECAR = $sidecarPython
        $env:VITRINE_OBJECT_SIDECAR_ARGS_JSON = ConvertTo-Json -Compress -InputObject @(
            $runner, '--sidecar-root', $external,
            '--core-python', $python,
            '--core-importer', (Join-Path $PSScriptRoot 'import_sidecar_splats.py'),
            '--max-frames', '40', '--prompts', 'radio'
        )
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
