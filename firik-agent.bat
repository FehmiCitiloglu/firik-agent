@echo off
REM firik-agent masaüstü uygulaması başlatıcısı (Windows)
cd /d "%~dp0"
if exist ".venv\Scripts\firik-gui.exe" (
    start "" ".venv\Scripts\firik-gui.exe"
) else (
    start "" python -m firik_agent.ui
)
