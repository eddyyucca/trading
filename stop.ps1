# AEGIS - PowerShell Stopper
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Menghentikan Server AEGIS (Port 8010)..." -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$connections = Get-NetTCPConnection -LocalPort 8010 -State Listen -ErrorAction SilentlyContinue
if ($connections) {
    foreach ($conn in $connections) {
        $pidToKill = $conn.OwningProcess
        Write-Host "[*] Menghentikan proses PID: $pidToKill ..." -ForegroundColor Yellow
        Stop-Process -Id $pidToKill -Force -ErrorAction SilentlyContinue
    }
    Write-Host "[+] Server AEGIS berhasil dihentikan!" -ForegroundColor Green
} else {
    Write-Host "[i] Tidak ada server AEGIS yang sedang berjalan di port 8010." -ForegroundColor Gray
}
Start-Sleep -Seconds 1
