$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $ProjectRoot

if (-not (Get-Command pm2 -ErrorAction SilentlyContinue)) {
    throw "pm2 is not installed. Run: npm install -g pm2"
}

if (-not (Test-Path ".\.venv\Scripts\memocore.exe")) {
    throw "Missing .venv\Scripts\memocore.exe. Run: .\.venv\Scripts\pip install -e `".[dev]`""
}

pm2 describe memocore-ste *> $null
if ($LASTEXITCODE -eq 0) {
    pm2 restart memocore-ste --update-env
} else {
    pm2 start ecosystem.config.cjs --only memocore-ste
}

pm2 save
