#!/usr/bin/env bash
# Firik Agent — Unix/Linux/macOS Installer
set -euo pipefail

# Keep installation logic in install.py so every entry point configures
# the same isolated venv, command launchers, and persistent user PATH.
EVREN_INSTALL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "EVREN kurulumu: evren ve firik-agent PATH'e eklenecek; venv aktivasyonu gerekmeyecek."
echo "Kurulumdan sonra yeni terminal açın veya gösterilen PATH komutunu bir kez çalıştırın."

# exec preserves the Python installer's exit status and forwarded arguments.
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$EVREN_INSTALL_DIR/install.py" "$@"
elif command -v python >/dev/null 2>&1; then
    exec python "$EVREN_INSTALL_DIR/install.py" "$@"
else
    echo "Error: Python 3 is required but not found in PATH." >&2
    echo "Please install Python 3.10+ (or 'brew install python' / 'sudo apt install python3')." >&2
    exit 1
fi
