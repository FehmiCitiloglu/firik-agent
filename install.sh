#!/usr/bin/env bash
# Firik Agent — Unix/Linux/macOS Installer
set -euo pipefail

# Keep installation logic in install.py so every entry point configures
# the same isolated venv, command launchers, and persistent user PATH.
FIRIK_INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ " $* " == *" --hybrid"* || " $* " == *" --desktop=hybrid"* || " $* " == *" --desktop hybrid"* || " $* " == *" --desktop=both"* || " $* " == *" --desktop both"* ]]; then
    echo "Firik hibrit kurulumu: firik, firik-agent, firik-gui ve firik-hybrid PATH'e eklenecek; TypeScript/Electron arayüzü kurulacak."
else
    echo "Firik kurulumu: firik, firik-agent ve firik-gui PATH'e eklenecek; venv aktivasyonu gerekmeyecek."
fi
echo "Kurulumdan sonra yeni terminal açın veya gösterilen PATH komutunu bir kez çalıştırın."

# Find a suitable Python 3.10+ executable
PYTHON_BIN=""
for py in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
        if "$py" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
            PYTHON_BIN="$py"
            break
        fi
    fi
done

if [ -n "$PYTHON_BIN" ]; then
    exec "$PYTHON_BIN" "$FIRIK_INSTALL_DIR/install.py" "$@"
else
    echo "Error: Python 3.10+ is required but not found in PATH." >&2
    echo "Please install Python 3.10+ (or 'brew install python@3.12' / 'sudo apt install python3')." >&2
    exit 1
fi
