# Firik Agent — Windows PowerShell Installer
$ErrorActionPreference = "Stop"
# install.py owns venv creation, command launchers, and persistent user PATH.
$FirikInstaller = Join-Path $PSScriptRoot "install.py"

Write-Host "Firik Agent Windows Kurulumu başlatılıyor..." -ForegroundColor Cyan
Write-Host "firik, firik-agent ve firik-gui PATH'e eklenecek; venv aktivasyonu gerekmeyecek." -ForegroundColor Cyan

if (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 $FirikInstaller @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    python $FirikInstaller @args
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    python3 $FirikInstaller @args
} else {
    Write-Host "Python 3 bulunamadı. Lütfen Python 3.10+ kurun: winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}

# Native process failures do not consistently trigger ErrorActionPreference.
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# A .ps1 runs in this PowerShell process, so commands can work immediately.
$FirikLocalAppData = $env:LOCALAPPDATA
if (-not $FirikLocalAppData) {
    $FirikLocalAppData = Join-Path $HOME "AppData\Local"
}
$FirikBin = Join-Path $FirikLocalAppData "FIRIK\bin"
if (($env:Path -split ';') -notcontains $FirikBin) {
    $env:Path = "$FirikBin;$env:Path"
}
Write-Host "Bu PowerShell oturumu hazır: firik --help veya firik-agent çalıştırabilirsiniz." -ForegroundColor Green
