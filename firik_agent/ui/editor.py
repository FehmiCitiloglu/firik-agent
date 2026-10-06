"""Open local projects and files in an external editor without shell interpolation."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

EDITORS = ["VS Code", "Cursor", "Zed", "Sistem Varsayılanı", "Özel Editör"]
_COMMANDS = {"VS Code": "code", "Cursor": "cursor", "Zed": "zed"}
_MAC_APPS = {"VS Code": "Visual Studio Code", "Cursor": "Cursor", "Zed": "Zed"}


def editor_command(editor: str, target: Path, custom_path: str = "") -> list[str]:
    if editor == "Özel Editör":
        executable = Path(custom_path)
        if not custom_path or not executable.is_absolute() or not executable.exists():
            raise ValueError("Özel editörün uygulama dosyasını seçin.")
        if sys.platform == "darwin" and executable.suffix == ".app":
            return ["open", "-a", str(executable), str(target)]
        if not executable.is_file() or executable.suffix.lower() in {".bat", ".cmd"}:
            raise ValueError("Editörün çalıştırılabilir uygulama dosyasını seçin.")
        return [str(executable), str(target)]
    if editor == "Sistem Varsayılanı":
        return ["open" if sys.platform == "darwin" else "xdg-open", str(target)]
    if editor not in _COMMANDS:
        raise ValueError("Bilinmeyen editör.")
    if sys.platform == "win32":
        app_dir, exe = {"VS Code": ("Microsoft VS Code", "Code.exe"),
                        "Cursor": ("cursor", "Cursor.exe"), "Zed": ("Zed", "Zed.exe")}[editor]
        for base in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
                     Path(os.environ.get("PROGRAMFILES", ""))):
            candidate = base / app_dir / exe
            if candidate.is_absolute() and candidate.is_file():
                return [str(candidate), str(target)]
    command = shutil.which(_COMMANDS[editor])
    if command and Path(command).suffix.lower() not in {".bat", ".cmd"}:
        return [command, str(target)]
    if sys.platform == "darwin":
        app = _MAC_APPS[editor]
        if any((base / f"{app}.app").exists() for base in (Path("/Applications"), Path.home() / "Applications")):
            return ["open", "-a", app, str(target)]
    raise ValueError(f"{editor} bulunamadı. Editörü kurun veya 'Özel Editör' ile uygulama dosyasını seçin.")


def open_in_editor(editor: str, target: str | Path, custom_path: str = "") -> None:
    target = Path(target).resolve()
    if not target.exists():
        raise ValueError("Açılacak dosya veya proje klasörü bulunamadı.")
    if editor == "Sistem Varsayılanı" and sys.platform == "win32":
        os.startfile(str(target))
        return
    subprocess.Popen(editor_command(editor, target, custom_path), shell=False,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
