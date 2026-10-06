#!/usr/bin/env python3
"""
Firik Agent — Cross-Platform Installer
Supports: macOS, Linux, and Windows (PowerShell / CMD)
Requirements: Python 3.10+
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)
COMMANDS = ("firik", "firik-agent", "firik-gui")


def install_commands(venv_dir: Path, bin_dir: Path, is_windows: bool):
    """Expose only Firik commands; their launchers retain the venv interpreter."""
    scripts_dir = venv_dir / ("Scripts" if is_windows else "bin")
    suffix = ".exe" if is_windows else ""
    pairs = [(scripts_dir / (name + suffix), bin_dir / (name + suffix)) for name in COMMANDS]
    for source, target in pairs:
        if not source.is_file():
            raise RuntimeError(f"Installed command missing: {source}")
        if not is_windows and target.exists() and not target.is_symlink():
            raise RuntimeError(f"Cannot replace existing command: {target}. Move it aside and retry.")
    bin_dir.mkdir(parents=True, exist_ok=True)
    for source, target in pairs:
        if is_windows:
            # pip's Windows launchers embed the absolute venv Python path.
            shutil.copy2(source, target)
        else:
            if target.is_symlink():
                try:
                    if target.resolve() == source.resolve():
                        continue
                except OSError:
                    pass
                target.unlink()
            target.symlink_to(source)


def add_shell_path(path: Path, line: str):
    """Append an idempotent PATH entry without replacing existing shell settings."""
    contents = path.read_text() if path.exists() else ""
    if line in contents.splitlines():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        stream.write(f"\n# FIRIK CLI (no virtual environment activation required)\n{line}\n")


def configure_path(bin_dir: Path, is_windows: bool) -> list[str]:
    """Persist the user PATH and return instructions for already-open terminals."""
    if is_windows:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            try:
                current, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, value_type = "", winreg.REG_EXPAND_SZ
            entries = [os.path.normcase(os.path.expandvars(p.strip('"'))) for p in current.split(";")]
            if os.path.normcase(str(bin_dir)) not in entries:
                winreg.SetValueEx(key, "Path", 0, value_type, str(bin_dir) + (";" + current if current else ""))
        # Let Explorer and newly launched terminals see the changed user PATH.
        import ctypes

        result = ctypes.c_size_t()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, ctypes.c_wchar_p("Environment"), 0x0002, 5000,
            ctypes.byref(result),
        )
        ps_path = str(bin_dir).replace("'", "''")
        return [f"PowerShell: $env:Path = '{ps_path};' + $env:Path", f'CMD: set "PATH={bin_dir};%PATH%"']

    home = Path.home()
    shell = Path(os.environ.get("SHELL", "/bin/sh")).name
    quoted = shlex.quote(str(bin_dir))
    line = f'case ":$PATH:" in *:{quoted}:*) ;; *) export PATH={quoted}:"$PATH" ;; esac'
    if shell == "zsh":
        zdotdir = Path(os.environ.get("ZDOTDIR") or home)
        profiles = [zdotdir / ".zshrc", zdotdir / ".zprofile"]
    elif shell == "bash":
        login = next((home / name for name in (".bash_profile", ".bash_login", ".profile")
                      if (home / name).exists()), home / ".profile")
        profiles = [home / ".bashrc", login]
    elif shell == "fish":
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        fish_path = str(bin_dir).replace("\\", "\\\\").replace("'", "\\'")
        line = f"contains -- '{fish_path}' $PATH; or set -gx PATH '{fish_path}' $PATH"
        profiles = [config / "fish" / "conf.d" / "firik.fish"]
    else:
        profiles = [home / ".profile"]
        log(f"  PATH saved in ~/.profile; configure {shell}'s startup file if it does not read this file.", "yellow")
    for profile in profiles:
        add_shell_path(profile, line)
    return [line if shell == "fish" else f'export PATH={quoted}:"$PATH"']


def log(msg: str, color: str = ""):
    colors = {
        "green": "\033[92m",
        "cyan": "\033[96m",
        "yellow": "\033[93m",
        "red": "\033[91m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "reset": "\033[0m",
    }
    # On Windows without VT terminal support, print plain text
    if sys.platform == "win32" and not os.environ.get("WT_SESSION") and not os.environ.get("TERM"):
        print(msg)
    else:
        prefix = colors.get(color, "")
        reset = colors["reset"] if color else ""
        print(f"{prefix}{msg}{reset}")


def check_python_version():
    if sys.version_info < MIN_PYTHON:
        log(f"Error: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required.", "red")
        log(f"Current version: {sys.version}", "yellow")
        sys.exit(1)
    log(f"✔ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} detected", "green")


def main():
    root_dir = Path(__file__).resolve().parent
    os.chdir(root_dir)

    log("\n" + "=" * 55, "cyan")
    log("  🚀 Firik Agent — Installation Setup", "bold")
    log("=" * 55, "cyan")

    check_python_version()

    # Detect UV or fallback to python -m venv
    uv_bin = shutil.which("uv")
    is_windows = sys.platform == "win32"
    venv_dir = root_dir / ".venv"

    # Virtual environment paths
    if is_windows:
        venv_python = venv_dir / "Scripts" / "python.exe"
        bin_dir = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "FIRIK" / "bin"
    else:
        venv_python = venv_dir / "bin" / "python"
        bin_dir = Path.home() / ".local" / "bin"

    # Step 1: Create venv
    if not venv_dir.exists():
        log("\n[1/3] Creating virtual environment (.venv)...", "cyan")
        if uv_bin:
            log("  Using 'uv venv'...", "dim")
            subprocess.run([uv_bin, "venv", "--python", sys.executable, str(venv_dir)], check=True)
        else:
            log("  Using 'python -m venv'...", "dim")
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        log("✔ Virtual environment created at .venv", "green")
    else:
        log("\n[1/3] Existing virtual environment detected (.venv)", "green")

    # Step 2: Install dependencies & package in editable mode
    log("\n[2/3] Installing dependencies and package...", "cyan")
    if uv_bin:
        log("  Running 'uv pip install -e .[dev]'...", "dim")
        subprocess.run([uv_bin, "pip", "install", "--python", str(venv_python), "-e", ".[dev]"], check=True)
    else:
        log("  Upgrading pip...", "dim")
        subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        log("  Running 'pip install -e .[dev]'...", "dim")
        subprocess.run([str(venv_python), "-m", "pip", "install", "-e", ".[dev]"], check=True)
    log("✔ Package firik-agent and dependencies installed successfully", "green")
    try:
        tk_check = subprocess.run([str(venv_python), "-c", "import tkinter"], capture_output=True)
        if tk_check and getattr(tk_check, "returncode", 0) != 0:
            log("  ℹ Bilgi: 'tkinter' bulunamadı; masaüstü arayüzü (firik-gui) için Tkinter gereklidir.", "yellow")
            if sys.platform == "darwin":
                log(f"    Kurulum: brew install python-tk@{sys.version_info.major}.{sys.version_info.minor}", "dim")
            elif sys.platform.startswith("linux"):
                log("    Kurulum: sudo apt install python3-tk", "dim")
    except Exception:
        pass

    log("\n[3/3] Making Firik commands available without activation...", "cyan")
    install_commands(venv_dir, bin_dir, is_windows)
    refresh_commands = configure_path(bin_dir, is_windows)
    log(f"✔ Commands installed in {bin_dir}", "green")

    # Done! Display instructions
    log("\n" + "=" * 55, "green")
    log("  🎉 Installation Complete!", "bold")
    log("=" * 55, "green")

    log("\n📌 Next Steps:", "cyan")
    log("  No virtual environment activation needed.", "green")
    log("  1. Open a new terminal, or refresh PATH in this terminal once:", "yellow")
    for command in refresh_commands:
        log(f"     {command}", "bold")

    log("\n  2. Run firik or firik-agent. First launch asks for your API key.", "yellow")
    log("     The key is stored in your OS credential store; no .env needed.", "dim")
    log("     firik login            (Save or replace your API key)", "bold")

    log("\n  3. Test & Run:", "yellow")
    log("     firik --help           (Direct FIRIK API CLI)", "bold")
    log("     firik-agent            (Interactive Agent REPL)", "bold")
    log("     firik-agent --commands (List all available commands)\n", "bold")


if __name__ == "__main__":
    main()
