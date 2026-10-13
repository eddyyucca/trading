@echo off
cd /d "%~dp0"
echo ============================================================
echo   Menghentikan Server AEGIS (Port 8010)...
echo ============================================================
echo.

for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8010 ^| findstr LISTENING') do (
    echo [*] Menghentikan proses PID: %%a ...
    taskkill /F /PID %%a >nul 2>&1
)

echo [+] Server AEGIS berhasil dihentikan!
timeout /t 2 >nul
