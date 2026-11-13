#!/usr/bin/env python3
"""
Firik Agent — Cross-Platform Installer
Supports: macOS, Linux, and Windows (PowerShell / CMD)
Requirements: Python 3.10+
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_PYTHON = (3, 10)
MIN_NODE = (22, 12)
MAX_NODE_MAJOR = 27
DEFAULT_YARN_VERSION = "4.18.1"
COMMANDS = ("firik", "firik-agent", "firik-gui")
HYBRID_COMMAND = "firik-hybrid"
HYBRID_LAUNCHER_MARKER = "Firik Agent hybrid desktop launcher"


def _default_bin_dir(is_windows: bool) -> Path:
    if is_windows:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "FIRIK" / "bin"
    return Path.home() / ".local" / "bin"


def _hybrid_command_path(bin_dir: Path, is_windows: bool) -> Path:
    return bin_dir / (f"{HYBRID_COMMAND}.cmd" if is_windows else HYBRID_COMMAND)


def _is_managed_hybrid_command(path: Path, is_windows: bool) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    marker = f"rem {HYBRID_LAUNCHER_MARKER}" if is_windows else f"# {HYBRID_LAUNCHER_MARKER}"
    return marker in path.read_text(encoding="utf-8", errors="ignore").splitlines()


def _validate_hybrid_command_target(bin_dir: Path, is_windows: bool) -> Path:
    target = _hybrid_command_path(bin_dir, is_windows)
    if (target.exists() or target.is_symlink()) and not _is_managed_hybrid_command(target, is_windows):
        raise RuntimeError(f"Cannot replace existing command: {target}. Move it aside and retry.")
    return target


def install_hybrid_command(executable: Path, bin_dir: Path, is_windows: bool) -> Path:
    """Install a PATH launcher for the packaged TypeScript/Electron desktop app."""
    if not executable.is_file():
        raise RuntimeError(f"Installed hybrid executable missing: {executable}")
    target = _validate_hybrid_command_target(bin_dir, is_windows)
    bin_dir.mkdir(parents=True, exist_ok=True)
    if is_windows:
        contents = f'@echo off\r\nrem {HYBRID_LAUNCHER_MARKER}\r\n"{executable}" %*\r\n'
        target.write_text(contents, encoding="utf-8")
    else:
        contents = f'#!/bin/sh\n# {HYBRID_LAUNCHER_MARKER}\nexec {shlex.quote(str(executable))} "$@"\n'
        target.write_text(contents, encoding="utf-8")
        target.chmod(0o755)
    return target


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



def _validate_desktop_platform(platform: str) -> None:
    if platform not in ("darwin", "win32") and not platform.startswith("linux"):
        raise RuntimeError(f"Desktop installation is not supported on {platform}")


def _install_desktop_build_deps(root_dir: Path, venv_python: Path, uv_bin: str | None) -> None:
    packages = ["-e", f"{root_dir}[desktop-build]"]
    if uv_bin:
        subprocess.run([uv_bin, "pip", "install", "--python", str(venv_python), *packages], check=True)
    else:
        subprocess.run([str(venv_python), "-m", "pip", "install", *packages], check=True)


def _desktop_data_dir(platform: str, subdir: str = "desktop") -> Path:
    if platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "FIRIK"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "firik-agent"
    data_dir = base / subdir
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _replace_artifact(source: Path, target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.exists() or target.is_symlink():
        target.unlink()
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


def _escape_desktop_value(path: Path) -> str:
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
        .replace("%", "%%")
    )


def _register_desktop_launcher(
    platform: str,
    app_title: str,
    desktop_filename: str,
    target: Path,
    executable: Path,
    icon: Path | None = None,
) -> Path:
    if platform == "darwin":
        applications = Path.home() / "Applications"
        applications.mkdir(parents=True, exist_ok=True)
        launcher = applications / f"{app_title}.app"
        if launcher.exists() and not launcher.is_symlink():
            raise RuntimeError(f"Cannot replace existing application: {launcher}")
        if launcher.is_symlink():
            if launcher.resolve() != target.resolve():
                raise RuntimeError(f"Cannot replace unrelated application link: {launcher}")
            launcher.unlink()
        launcher.symlink_to(target, target_is_directory=True)
        return launcher
    if platform == "win32":
        programs = Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "Microsoft/Windows/Start Menu/Programs"
        programs.mkdir(parents=True, exist_ok=True)
        launcher = programs / f"{app_title}.lnk"
        env = os.environ.copy()
        env.update(FIRIK_DESKTOP_TARGET=str(executable), FIRIK_DESKTOP_LINK=str(launcher))
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:FIRIK_DESKTOP_LINK); "
                "$s.TargetPath=$env:FIRIK_DESKTOP_TARGET; $s.WorkingDirectory=Split-Path $s.TargetPath; $s.Save()",
            ],
            env=env,
            check=True,
        )
        return launcher
    applications = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    launcher = applications / desktop_filename
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={app_title}",
        f'Exec="{_escape_desktop_value(executable)}"',
    ]
    if icon is not None and icon.is_file():
        lines.append(f"Icon={icon}")
    lines.extend(["Terminal=false", "Categories=Utility;Development;", ""])
    launcher.write_text("\n".join(lines), encoding="utf-8")
    return launcher


def install_desktop(root_dir: Path, venv_python: Path, uv_bin: str | None, platform: str) -> Path:
    """Build the legacy Python/Tk desktop bundle and register a per-user launcher."""
    _validate_desktop_platform(platform)
    tk = subprocess.run([str(venv_python), "-c", "import tkinter"], capture_output=True, text=True)
    if tk.returncode:
        raise RuntimeError("Tkinter is required for the desktop app. Install the Tk package for your Python and retry.")
    _install_desktop_build_deps(root_dir, venv_python, uv_bin)
    if platform.startswith("linux"):
        log("  Linux microphone support requires PortAudio (for example: sudo apt install libportaudio2).", "dim")
    data_dir = _desktop_data_dir(platform, "desktop")
    with tempfile.TemporaryDirectory(prefix="firik-desktop-") as tmp:
        temp = Path(tmp)
        env = os.environ.copy()
        env["PYINSTALLER_CONFIG_DIR"] = str(temp / "cache")
        subprocess.run([str(venv_python), "-m", "PyInstaller", str(root_dir / "firik-agent.spec"),
                        "--clean", "--noconfirm", "--workpath", str(temp / "build"),
                        "--distpath", str(temp / "dist")], cwd=root_dir, env=env, check=True)
        name = "firik-agent.app" if platform == "darwin" else "firik-agent.exe" if platform == "win32" else "firik-agent"
        source, target = temp / "dist" / name, data_dir / name
        if not source.exists():
            raise RuntimeError(f"Desktop build did not produce {source}")
        _replace_artifact(source, target)
    return _register_desktop_launcher(
        platform,
        "Firik Agent (Source Install)",
        "firik-agent-source.desktop",
        target,
        target,
    )


def _find_node_toolchain(root_dir: Path) -> tuple[str, str, str]:
    """Verify Node.js >= 22.12 and npm are available, returning (node, npm, yarn_version)."""
    node_bin = shutil.which("node")
    npm_bin = shutil.which("npm")
    if not node_bin or not npm_bin:
        raise RuntimeError("Node.js 22.12+ and npm are required for the TypeScript/Electron hybrid desktop app.")
    probe = subprocess.run([node_bin, "-p", "process.versions.node"], capture_output=True, text=True)
    if probe.returncode != 0:
        raise RuntimeError("Failed to determine Node.js version.")
    version_str = probe.stdout.strip()
    try:
        major, minor = (int(part) for part in version_str.split(".")[:2])
    except ValueError as err:
        raise RuntimeError(f"Unrecognized Node.js version string: {version_str}") from err
    if (major, minor) < MIN_NODE:
        raise RuntimeError(
            f"Node.js >= {MIN_NODE[0]}.{MIN_NODE[1]} is required for the hybrid desktop app (found {version_str})."
        )
    if major >= MAX_NODE_MAJOR:
        log(f"  Note: package.json targets Node < {MAX_NODE_MAJOR}; continuing with Node {version_str}.", "yellow")
    yarn_version = DEFAULT_YARN_VERSION
    pkg_json = root_dir / "package.json"
    if pkg_json.is_file():
        pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
        pm = pkg_data.get("packageManager", "")
        if isinstance(pm, str) and pm.startswith("yarn@"):
            yarn_version = pm.split("@", 1)[1]
    return node_bin, npm_bin, yarn_version


def _ensure_hybrid_js_dependencies(root_dir: Path, npm_bin: str, yarn_version: str) -> None:
    yarn_bin = shutil.which("yarn")
    if yarn_bin:
        ver = subprocess.run([yarn_bin, "--version"], capture_output=True, text=True)
        if ver.returncode == 0 and ver.stdout.strip().startswith(f"{yarn_version.split('.')[0]}."):
            subprocess.run([yarn_bin, "install", "--immutable"], cwd=root_dir, check=True)
            return
    subprocess.run(
        [npm_bin, "exec", "--yes", f"--package=@yarnpkg/cli-dist@{yarn_version}", "--", "yarn", "install", "--immutable"],
        cwd=root_dir,
        check=True,
    )


def _locate_hybrid_package(dist_dir: Path, platform: str) -> tuple[Path, Path]:
    """Return (source_bundle_or_dir, relative_executable) inside dist/hybrid-desktop."""
    if platform == "darwin":
        candidates = [dist_dir / folder / "Firik Agent Hybrid.app" for folder in ("mac-arm64", "mac-universal", "mac")]
        candidates.extend(sorted(dist_dir.glob("mac*/Firik Agent Hybrid.app")))
        for candidate in candidates:
            if candidate.is_dir():
                return candidate, Path("Contents/MacOS/Firik Agent Hybrid")
    elif platform == "win32":
        candidates = [dist_dir / "win-unpacked", *sorted(dist_dir.glob("win*-unpacked"))]
        for candidate in candidates:
            if (candidate / "Firik Agent Hybrid.exe").is_file():
                return candidate, Path("Firik Agent Hybrid.exe")
    else:
        candidates = [dist_dir / "linux-unpacked", *sorted(dist_dir.glob("linux*-unpacked"))]
        for candidate in candidates:
            if not candidate.is_dir():
                continue
            for exe_name in ("firik-agent-hybrid", "firik-desktop", "desktop", "Firik Agent Hybrid"):
                if (candidate / exe_name).is_file():
                    return candidate, Path(exe_name)
    raise RuntimeError(f"Hybrid desktop build did not produce an application bundle in {dist_dir}")


def install_hybrid_desktop(
    root_dir: Path,
    venv_python: Path,
    uv_bin: str | None,
    platform: str,
    bin_dir: Path | None = None,
) -> Path:
    """Build the TypeScript/Electron hybrid desktop app and register its launchers."""
    _validate_desktop_platform(platform)
    is_windows = platform == "win32"
    target_bin_dir = bin_dir if bin_dir is not None else _default_bin_dir(is_windows)
    _validate_hybrid_command_target(target_bin_dir, is_windows)
    _, npm_bin, yarn_version = _find_node_toolchain(root_dir)
    _install_desktop_build_deps(root_dir, venv_python, uv_bin)
    _ensure_hybrid_js_dependencies(root_dir, npm_bin, yarn_version)
    env = os.environ.copy()
    env["FIRIK_DESKTOP_PYTHON"] = str(venv_python)
    subprocess.run([npm_bin, "run", "desktop:package"], cwd=root_dir, env=env, check=True)
    source, rel_executable = _locate_hybrid_package(root_dir / "dist" / "hybrid-desktop", platform)
    data_dir = _desktop_data_dir(platform, "hybrid-desktop")
    target = data_dir / ("Firik Agent Hybrid.app" if platform == "darwin" else "Firik Agent Hybrid")
    _replace_artifact(source, target)
    executable = target / rel_executable
    install_hybrid_command(executable, target_bin_dir, is_windows)
    icon = target / "resources" / "icon.png" if platform.startswith("linux") else None
    return _register_desktop_launcher(
        platform,
        "Firik Agent Hybrid (Source Install)",
        "firik-agent-hybrid-source.desktop",
        target,
        executable,
        icon=icon,
    )

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


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Firik Agent Installer")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--hybrid",
        action="store_const",
        dest="desktop",
        const="hybrid",
        help="Install the TypeScript/Electron hybrid desktop UI instead of the Python/Tk UI.",
    )
    group.add_argument(
        "--desktop",
        choices=("python", "hybrid", "both", "none"),
        help="Choose which desktop UI to build and install (default: python).",
    )
    parser.set_defaults(desktop="python")
    return parser.parse_args([] if argv is None else list(argv))


def main(argv: Sequence[str] | None = None):
    args = _parse_args(argv)
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
    venv_python = venv_dir / ("Scripts/python.exe" if is_windows else "bin/python")
    bin_dir = _default_bin_dir(is_windows)

    # Step 1: Create venv
    if not venv_dir.exists():
        log("\n[1/4] Creating virtual environment (.venv)...", "cyan")
        if uv_bin:
            log("  Using 'uv venv'...", "dim")
            subprocess.run([uv_bin, "venv", "--python", sys.executable, str(venv_dir)], check=True)
        else:
            log("  Using 'python -m venv'...", "dim")
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        log("✔ Virtual environment created at .venv", "green")
    else:
        log("\n[1/4] Existing virtual environment detected (.venv)", "green")

    # Step 2: Install dependencies & package in editable mode
    log("\n[2/4] Installing dependencies and package...", "cyan")
    if uv_bin:
        log("  Running 'uv pip install -e .[dev]'...", "dim")
        subprocess.run([uv_bin, "pip", "install", "--python", str(venv_python), "-e", ".[dev]"], check=True)
    else:
        log("  Upgrading pip...", "dim")
        subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        log("  Running 'pip install -e .[dev]'...", "dim")
        subprocess.run([str(venv_python), "-m", "pip", "install", "-e", ".[dev]"], check=True)
    log("✔ Package firik-agent and dependencies installed successfully", "green")
    log("\n[3/4] Making Firik commands available without activation...", "cyan")
    install_commands(venv_dir, bin_dir, is_windows)
    refresh_commands = configure_path(bin_dir, is_windows)
    log(f"✔ Commands installed in {bin_dir}", "green")

    python_launcher: Path | None = None
    hybrid_launcher: Path | None = None
    if args.desktop == "none":
        log("\n[4/4] Skipping desktop application installation (--desktop none).", "dim")
    else:
        log("\n[4/4] Building and installing the desktop application...", "cyan")
        if args.desktop in ("python", "both"):
            python_launcher = install_desktop(root_dir, venv_python, uv_bin, sys.platform)
            log(f"✔ Python/Tk desktop application installed: {python_launcher}", "green")
        if args.desktop in ("hybrid", "both"):
            hybrid_launcher = install_hybrid_desktop(root_dir, venv_python, uv_bin, sys.platform, bin_dir=bin_dir)
            log(f"✔ TypeScript/Electron hybrid desktop installed: {hybrid_launcher}", "green")

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
    log("     firik-gui              (Legacy Python/Tk reference UI)", "bold")
    if python_launcher is not None:
        log(f"     Python/Tk launcher:    {python_launcher}", "bold")
    if hybrid_launcher is not None:
        log("     firik-hybrid           (TypeScript/Electron hybrid desktop)", "bold")
        log(f"     Hybrid launcher:       {hybrid_launcher}", "bold")
    elif args.desktop == "python":
        log("     Tip: run 'make install-hybrid' for the TypeScript/Electron hybrid UI.", "dim")
    log("     firik-agent --commands (List all available commands)\n", "bold")


if __name__ == "__main__":
    main(sys.argv[1:])
