$ErrorActionPreference = "Stop"

if (-not (Get-Command pm2 -ErrorAction SilentlyContinue)) {
    throw "pm2 is not installed. Run: npm install -g pm2"
}

pm2 logs memocore-ste --lines 100
