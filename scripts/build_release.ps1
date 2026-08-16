[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$FrontendRoot = Join-Path $RepoRoot "web\release"
$PackageLock = Join-Path $RepoRoot "package-lock.json"
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command exited with code $LASTEXITCODE"
    }
}

$Npm = (Get-Command npm -ErrorAction Stop).Source
$Python = (Get-Command python -ErrorAction Stop).Source

if (Test-Path -LiteralPath $OutputDirectory -PathType Leaf) {
    throw "OutputDirectory must be a directory: $OutputDirectory"
}
$RepoPrefix = $RepoRoot.TrimEnd('\') + '\'
if ($OutputDirectory.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "OutputDirectory must be outside the source repository."
}
if (-not (Test-Path -LiteralPath $PackageLock -PathType Leaf)) {
    throw "package-lock.json is required for the release build."
}
if (Test-Path -LiteralPath $FrontendRoot) {
    throw "web/release must be absent before the release build; use a fresh sanitized source tree."
}
if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}
if ((Get-ChildItem -LiteralPath $OutputDirectory -Force | Select-Object -First 1)) {
    throw "OutputDirectory must be empty for a deterministic build: $OutputDirectory"
}

Push-Location $RepoRoot
try {
    # Required order: npm ci -> npm audit -> npm test -> npm run typecheck -> npm run lint -> npm run build -> frontend closure -> python build.
    Invoke-Checked -Command $Npm -Arguments @("ci")
    Invoke-Checked -Command $Npm -Arguments @("audit", "--audit-level=high")
    Invoke-Checked -Command $Npm -Arguments @("test")
    Invoke-Checked -Command $Npm -Arguments @("run", "typecheck")
    Invoke-Checked -Command $Npm -Arguments @("run", "lint")
    Invoke-Checked -Command $Npm -Arguments @("run", "build")

    if (-not (Test-Path -LiteralPath $FrontendRoot -PathType Container)) {
        throw "Frontend closure directory is missing: $FrontendRoot"
    }
    $FrontendFiles = @(Get-ChildItem -LiteralPath $FrontendRoot -File -Recurse)
    if ($FrontendFiles.Count -eq 0) {
        throw "Frontend closure is empty: $FrontendRoot"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "index.html") -PathType Leaf)) {
        throw "Frontend closure is missing index.html: $FrontendRoot"
    }

    # Build with: python -m build --wheel --sdist --outdir <OutputDirectory>
    Invoke-Checked -Command $Python -Arguments @(
        "-m", "build", "--wheel", "--sdist", "--outdir", $OutputDirectory
    )

    $ExpectedVersion = (& $Python -c "import pathlib, tomllib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8'))['project']['version'])").Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($ExpectedVersion)) {
        throw "Unable to read the package version from pyproject.toml."
    }
    Invoke-Checked -Command $Python -Arguments @(
        "scripts/verify_release_artifacts.py",
        "--dist-dir", $OutputDirectory,
        "--expected-version", $ExpectedVersion
    )
}
finally {
    Pop-Location
}
