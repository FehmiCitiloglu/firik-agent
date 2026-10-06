#!/bin/bash
# firik-agent masaüstü uygulaması başlatıcısı (macOS)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

if [ -f ".venv/bin/firik-gui" ]; then
    exec .venv/bin/firik-gui
elif command -v firik-gui >/dev/null 2>&1; then
    exec firik-gui
else
    exec python3 -m firik_agent.ui
fi
