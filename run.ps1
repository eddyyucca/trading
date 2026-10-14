# AEGIS - PowerShell Launcher
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  AEGIS -- Autonomous AI Quant Prediction & Trading Engine" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "[*] Membuka server di http://localhost:8010 ..." -ForegroundColor Green
Write-Host "[*] Tekan Ctrl+C untuk menghentikan server." -ForegroundColor Gray
Write-Host ""

& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8010
