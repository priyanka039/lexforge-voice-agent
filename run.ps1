# Convenience launcher (Windows PowerShell).
# Usage:  .\run.ps1
$ErrorActionPreference = "Stop"

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
  Write-Host "Creating virtual environment..." -ForegroundColor Cyan
  python -m venv .venv
  & $venvPython -m pip install --upgrade pip
  & $venvPython -m pip install -r backend\requirements.txt
}

if (-not (Test-Path (Join-Path $PSScriptRoot ".env"))) {
  Write-Host "No .env found — copying .env.example. Add your API keys!" -ForegroundColor Yellow
  Copy-Item .env.example .env
}

Write-Host "Starting LexForge Moot Court on http://127.0.0.1:8000" -ForegroundColor Green
& $venvPython -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
