# Firik Agent — Windows PowerShell Installer
$ErrorActionPreference = "Stop"
# install.py owns venv creation, command launchers, and persistent user PATH.
$EvrenInstaller = Join-Path $PSScriptRoot "install.py"

Write-Host "Firik Agent Windows Kurulumu başlatılıyor..." -ForegroundColor Cyan
Write-Host "evren ve firik-agent PATH'e eklenecek; venv aktivasyonu gerekmeyecek." -ForegroundColor Cyan

if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 $EvrenInstaller @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    python $EvrenInstaller @args
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    python3 $EvrenInstaller @args
} else {
    Write-Host "Python 3 bulunamadı. Lütfen Python 3.10+ kurun: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}

# Native process failures do not consistently trigger ErrorActionPreference.
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# A .ps1 runs in this PowerShell process, so commands can work immediately.
$EvrenLocalAppData = $env:LOCALAPPDATA
if (-not $EvrenLocalAppData) {
    $EvrenLocalAppData = Join-Path $HOME "AppData\Local"
}
$EvrenBin = Join-Path $EvrenLocalAppData "EVREN\bin"
if (($env:Path -split ';') -notcontains $EvrenBin) {
    $env:Path = "$EvrenBin;$env:Path"
}
Write-Host "Bu PowerShell oturumu hazır: evren --help veya firik-agent çalıştırabilirsiniz." -ForegroundColor Green
