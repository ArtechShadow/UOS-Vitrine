[CmdletBinding()]
param(
    [ValidateSet('Check', 'Prepare', 'Launch', 'Rehearse', 'Readiness')]
    [string]$Mode = 'Check',
    [string]$ProjectRoot = '',
    [Alias('Venv')]
    [string]$EnvironmentPath = '',
    [Alias('Python')]
    [string]$PythonPath = '',
    [switch]$Install,
    [string]$ConstraintsPath = '',
    [string]$Source = '',
    [string[]]$Originals = @(),
    [string]$RunDir = '',
    [int]$Port = 8765,
    [ValidateSet('draft', 'standard', 'archive', 'demo')]
    [string]$Quality = 'demo',
    [ValidateSet('auto', 'yes', 'no')]
    [string]$Gpu = 'auto',
    [ValidateSet('low', 'medium', 'high')]
    [string]$HardwareTarget = 'medium',
    [string]$HardwareConfig = '',
    [string]$ReadinessReport = '',
    [switch]$Resume,
    [switch]$Offline,
    [switch]$OpenBrowser,
    [switch]$WithObjects
)

$ErrorActionPreference = 'Stop'
$root = if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    Split-Path $PSScriptRoot -Parent
} else {
    (Resolve-Path -LiteralPath $ProjectRoot).Path
}

function Resolve-ProjectPath([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) { return '' }
    if ([IO.Path]::IsPathRooted($PathValue)) { return $PathValue }
    return Join-Path $root $PathValue
}

$environment = if (-not [string]::IsNullOrWhiteSpace($EnvironmentPath)) {
    Resolve-ProjectPath $EnvironmentPath
} elseif ($Mode -eq 'Prepare') {
    Join-Path $root '.venv-a6000'
} else {
    Join-Path $root '.venv'
}

$previousHardwareConfig = $env:VITRINE_HARDWARE_CONFIG
$previousOffline = $env:VITRINE_OFFLINE
$previousHfOffline = $env:HF_HUB_OFFLINE
if (-not [string]::IsNullOrWhiteSpace($HardwareConfig)) {
    $config = Resolve-ProjectPath $HardwareConfig
    if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw "Hardware config not found: $config" }
    $env:VITRINE_HARDWARE_CONFIG = (Resolve-Path -LiteralPath $config).Path
}

function Invoke-Checked([string]$Executable, [object[]]$Arguments) {
    & $Executable @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        throw "Command failed with exit code $code`: $Executable $($Arguments -join ' ')"
    }
}

function Resolve-Python {
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        $candidate = Resolve-ProjectPath $PythonPath
    } else {
        $candidate = Join-Path $environment 'Scripts\python.exe'
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Python 3.11 environment was not found at $candidate. Run: powershell -File scripts\a6000_rehearsal.ps1 -Mode Prepare"
    }
    return (Resolve-Path -LiteralPath $candidate).Path
}

function Invoke-Readiness([string]$Python, [switch]$AllowBlocked) {
    $script = Join-Path $PSScriptRoot 'rehearsal_readiness.py'
    $arguments = @(
        $script,
        '--project-root', $root,
        '--python', $Python,
        '--hardware-target', $HardwareTarget,
        '--port', "$Port"
    )
    $run = Resolve-ProjectPath $RunDir
    if (-not [string]::IsNullOrWhiteSpace($RunDir)) { $arguments += @('--run-dir', $run) }
    if (-not [string]::IsNullOrWhiteSpace($Source)) { $arguments += @('--source', (Resolve-ProjectPath $Source)) }
    if (-not [string]::IsNullOrWhiteSpace($ReadinessReport)) {
        $arguments += @('--output', (Resolve-ProjectPath $ReadinessReport))
    }
    if ($Offline) { $arguments += '--offline' }
    if ($WithObjects) {
        $sidecar = $env:VITRINE_OBJECT_SIDECAR
        if (-not [string]::IsNullOrWhiteSpace($sidecar)) { $arguments += @('--sidecar', $sidecar) }
    }
    & $Python @arguments
    $code = $LASTEXITCODE
    if ($code -ne 0 -and -not $AllowBlocked) {
        throw "Readiness checks failed; inspect the generated JSON report before continuing."
    }
    return $code
}

try {
switch ($Mode) {
    'Prepare' {
        if ($Install -and -not [string]::IsNullOrWhiteSpace($PythonPath)) {
            throw '-Install always uses the newly created environment; omit -PythonPath to avoid changing another environment.'
        }
        $created = $false
        if ($Install -and (Test-Path -LiteralPath $environment)) {
            throw "Refusing to install into an existing environment: $environment. Choose a new -EnvironmentPath."
        }
        if (-not (Test-Path -LiteralPath $environment)) {
            $py = Get-Command py -ErrorAction SilentlyContinue
            if ($null -eq $py) {
                throw 'The Python launcher (py) was not found. Install Python 3.11 for the current user, then rerun this mode.'
            }
            Write-Host "Creating isolated environment at $environment"
            Invoke-Checked $py.Path @('-3.11', '-m', 'venv', $environment)
            $created = $true
        } elseif (-not (Test-Path -LiteralPath (Join-Path $environment 'Scripts\python.exe') -PathType Leaf)) {
            throw "Existing environment is incomplete: $environment. Preserve it and choose a new -EnvironmentPath."
        } else {
            Write-Host "Keeping existing isolated environment: $environment"
        }
        if ($Install -and $created) {
            $python = Resolve-Python
            $requirements = Join-Path $root 'requirements.txt'
            $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--requirement', $requirements)
            if (-not [string]::IsNullOrWhiteSpace($ConstraintsPath)) {
                $constraints = Resolve-ProjectPath $ConstraintsPath
                if (-not (Test-Path -LiteralPath $constraints -PathType Leaf)) { throw "Constraints file not found: $constraints" }
                $pipArgs += @('--constraint', $constraints)
            }
            Write-Host 'Installing requirements only into this new isolated environment.'
            Invoke-Checked $python $pipArgs
        } elseif (-not $Install) {
            Write-Host 'Environment created/retained. No packages were installed; use -Install explicitly when online.'
        }
        break
    }

    'Check' {
        $python = Resolve-Python
        # Readiness is observational; a blocked result is expected on a host
        # without the A6000, Docker image, sidecar, or real capture.
        [void](Invoke-Readiness $python -AllowBlocked)
        break
    }

    'Readiness' {
        $python = Resolve-Python
        [void](Invoke-Readiness $python)
        break
    }

    'Launch' {
        $python = Resolve-Python
        $launcher = Join-Path $PSScriptRoot 'start_demo.ps1'
        $arguments = @{ PythonPath = $python; Port = $Port; HardwareTarget = $HardwareTarget }
        if (-not [string]::IsNullOrWhiteSpace($HardwareConfig)) { $arguments['HardwareConfig'] = $HardwareConfig }
        if ($Offline) { $arguments['Offline'] = $true }
        if ($OpenBrowser) { $arguments['OpenBrowser'] = $true }
        if ($WithObjects) { $arguments['WithObjects'] = $true }
        if (-not [string]::IsNullOrWhiteSpace($RunDir)) {
            $resolvedRun = Resolve-ProjectPath $RunDir
            if (-not (Test-Path -LiteralPath $resolvedRun -PathType Container)) {
                throw "Run directory was not found: $resolvedRun"
            }
            $arguments['RunsRoot'] = Split-Path -Parent $resolvedRun
            $arguments['Run'] = @([IO.Path]::GetFileName($resolvedRun.TrimEnd('\', '/')))
        }
        & $launcher @arguments
        if ($LASTEXITCODE -ne 0) { throw "Dashboard launcher exited with code $LASTEXITCODE" }
        break
    }

    'Rehearse' {
        if ([string]::IsNullOrWhiteSpace($Source)) { throw '-Source is required for a real-data rehearsal.' }
        $python = Resolve-Python
        $source = Resolve-ProjectPath $Source
        if (-not (Test-Path -LiteralPath $source)) { throw "Source path was not found: $source" }
        if ([string]::IsNullOrWhiteSpace($RunDir)) {
            $RunDir = Join-Path $root ('runs\a6000-rehearsal-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
        } else {
            $RunDir = Resolve-ProjectPath $RunDir
        }
        $arguments = @(
            '-m', 'vitrine',
            '--run-dir', $RunDir,
            '--quality', $Quality,
            'preflight',
            '--source', $source,
            '--gpu', $Gpu
        )
        Write-Host "Checking prerequisites for $source"
        Invoke-Checked $python $arguments

        $runArguments = @(
            '-m', 'vitrine',
            '--run-dir', $RunDir,
            '--quality', $Quality,
            'run',
            '--source', $source,
            '--selection-preset', 'balanced',
            '--gpu', $Gpu,
            '--capture-type', 'scene',
            '--title', 'XR Lab A6000 rehearsal',
            '--subject', 'Real-data acceptance rehearsal for the 30 September 2026 demonstration.'
        )
        $originalPaths = if ($Originals.Count -gt 0) {
            @($Originals | ForEach-Object { Resolve-ProjectPath $_ })
        } else {
            @($source)
        }
        foreach ($original in $originalPaths) {
            if (-not (Test-Path -LiteralPath $original)) {
                throw "Original preservation path was not found: $original"
            }
        }
        $runArguments += @('--originals') + $originalPaths
        if ($Resume) { $runArguments += '--resume' }
        if ($Offline) { $env:VITRINE_OFFLINE = '1'; $env:HF_HUB_OFFLINE = '1' }
        try {
            Write-Host "Running recoverable pipeline in $RunDir"
            Invoke-Checked $python $runArguments
        } finally {
            $reportArguments = @(
                (Join-Path $PSScriptRoot 'rehearsal_readiness.py'),
                '--project-root', $root,
                '--python', $python,
                '--hardware-target', $HardwareTarget,
                '--run-dir', $RunDir,
                '--source', $source,
                '--port', "$Port"
            )
            if (-not [string]::IsNullOrWhiteSpace($ReadinessReport)) {
                $reportArguments += @('--output', (Resolve-ProjectPath $ReadinessReport))
            }
            if ($Offline) { $reportArguments += '--offline' }
            & $python @reportArguments
            Write-Host "Readiness report written for $RunDir"
        }
        break
    }
}
} finally {
    $env:VITRINE_HARDWARE_CONFIG = $previousHardwareConfig
    $env:VITRINE_OFFLINE = $previousOffline
    $env:HF_HUB_OFFLINE = $previousHfOffline
}
