param(
    [Parameter(Mandatory = $true)]
    [string]$CoreWheel
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WheelPath = (Resolve-Path -LiteralPath $CoreWheel).Path
$Venv = Join-Path $RepoRoot ".venv"
$Python = Join-Path $Venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    python -m venv $Venv
}

& $Python (Join-Path $PSScriptRoot "verify_core_wheel.py") --wheel $WheelPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location $RepoRoot
try {
    $PackageLock = Join-Path $RepoRoot "package-lock.json"
    if (-not (Test-Path -LiteralPath $PackageLock -PathType Leaf)) {
        throw "package-lock.json is required; refusing to generate or repair the dependency lockfile."
    }
    npm ci
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    npm run build
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
finally {
    Pop-Location
}

& $Python -m pip install --requirement (Join-Path $RepoRoot "requirements.lock") --find-links (Split-Path -Parent $WheelPath)
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# The Core distribution version may stay constant while its reviewed interface changes.
# Install the digest-verified wheel explicitly so pip cannot reuse an older same-version build.
& $Python -m pip install --force-reinstall --no-deps $WheelPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python -m pip install --editable $RepoRoot --no-deps
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
