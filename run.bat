@echo off
cd /d "%~dp0"
echo ============================================================
echo   AEGIS -- Autonomous AI Quant Prediction ^& Trading Engine
echo ============================================================
echo.
echo [*] Membuka server di http://localhost:8010 ...
echo [*] Tekan Ctrl+C untuk menghentikan server.
echo.

".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8010
pause
