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

pm2 startOrReload ecosystem.config.cjs --only memocore-ste --update-env

pm2 save
