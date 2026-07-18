param(
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $ProjectRoot

if (-not (Get-Command pm2 -ErrorAction SilentlyContinue)) {
    throw "pm2 is not installed. Run: npm install -g pm2"
}

if (-not (Test-Path ".\.venv\Scripts\memocore.exe")) {
    throw "Missing .venv\Scripts\memocore.exe. Run: .\.venv\Scripts\pip install -e `".[dev]`""
}

if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    throw "Missing .venv\Scripts\python.exe. Run: py -m venv .venv"
}

$GitCommit = "unknown"
$GitDirty = "unknown"
if (Get-Command git -ErrorAction SilentlyContinue) {
    $GitCommit = (& git rev-parse --short=12 HEAD).Trim()
    $GitStatus = (& git status --porcelain)
    $IsDirty = -not [string]::IsNullOrWhiteSpace(($GitStatus -join "`n"))
    $GitDirty = if ($IsDirty) { "yes" } else { "no" }
    $AllowDirtyEnv = $env:MEMOCORE_ALLOW_DIRTY_DEPLOY -eq "1"
    if ($IsDirty -and -not $AllowDirty -and -not $AllowDirtyEnv) {
        throw "Working tree is dirty. Commit/stash changes before deploy, or rerun with -AllowDirty for an explicit development override."
    }
} else {
    Write-Warning "git was not found; deploy commit and dirty state will be stamped as unknown."
}

Write-Host "Checking Python syntax before restarting PM2..."
& ".\.venv\Scripts\python.exe" -m compileall -q "src\memocore"
if ($LASTEXITCODE -ne 0) {
    throw "Python compile check failed. Fix the syntax/import errors before restarting memocore-ste."
}

Write-Host "Running MemoCore doctor before restarting PM2..."
& ".\.venv\Scripts\memocore.exe" doctor
if ($LASTEXITCODE -ne 0) {
    throw "MemoCore doctor failed. Fix the reported issue before restarting memocore-ste."
}

$SchemaVersion = (& ".\.venv\Scripts\python.exe" -c "from pathlib import Path; from memocore.config import get_settings; from memocore.services.runtime_version_service import runtime_version_descriptor; print(runtime_version_descriptor(get_settings().database_path, repo_path=Path.cwd()).schema_version)").Trim()

$env:MEMOCORE_DEPLOY_COMMIT = $GitCommit
$env:MEMOCORE_DEPLOY_DIRTY = $GitDirty
$env:MEMOCORE_DEPLOY_SCHEMA = $SchemaVersion
$env:MEMOCORE_DEPLOYED_AT = (Get-Date).ToUniversalTime().ToString("o")

Write-Host "Stamping PM2 env: commit=$GitCommit dirty=$GitDirty schema=$SchemaVersion"

pm2 startOrReload ecosystem.config.cjs --only memocore-ste --update-env

pm2 save
