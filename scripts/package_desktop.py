"""Package a native PyInstaller build; invoked on each release runner."""
from importlib.metadata import version
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "release_assets"
VERSION = version("firik-agent")


def run(*args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def linux():
    stage = ROOT / "build/linux-package"
    (stage / "usr/bin").mkdir(parents=True, exist_ok=True)
    apps = stage / "usr/share/applications"
    icons = stage / "usr/share/icons/hicolor/256x256/apps"
    apps.mkdir(parents=True, exist_ok=True)
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "dist/firik-agent", stage / "usr/bin/firik-agent")
    (stage / "usr/bin/firik-agent").chmod(0o755)
    desktop = (ROOT / "firik-agent.desktop").read_text().replace("Exec=firik-gui", "Exec=firik-agent")
    (apps / "firik-agent.desktop").write_text(desktop)
    shutil.copy2(ROOT / "firik_agent/ui/assets/icon.png", icons / "firik-agent.png")
    with tarfile.open(OUT / f"firik-agent-{VERSION}-linux-x86_64.tar.gz", "w:gz") as archive:
        archive.add(stage / "usr", arcname="usr")
    control = stage / "DEBIAN"
    control.mkdir(exist_ok=True)
    (control / "control").write_text(f"""Package: firik-agent
Version: {VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: FIRIK <noreply@ssyz.org.tr>
Depends: libc6 (>= 2.35), libx11-6, libxext6, libxft2, libxrender1, libfontconfig1
Recommends: gnome-keyring
Description: Firik Agent desktop client
 Chat, OCR, transcription and reranking with FIRIK APIs.
""")
    run("dpkg-deb", "--root-owner-group", "--build", stage, OUT / f"firik-agent_{VERSION}_amd64.deb")
    # rpmbuild uses the same payload, with explicit runtime dependencies.
    top = ROOT / "build/rpm"
    for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
        (top / name).mkdir(parents=True, exist_ok=True)
    spec = top / "SPECS/firik-agent.spec"
    spec.write_text(f"""Name: firik-agent
Version: {VERSION}
Release: 1
Summary: Firik Agent desktop client
License: Proprietary
BuildArch: x86_64
AutoReqProv: no
Requires: glibc >= 2.35, libX11, libXext, libXft, libXrender, fontconfig
%description
Chat, OCR, transcription and reranking with FIRIK APIs.
%install
mkdir -p %{{buildroot}}/usr
cp -a "{stage}/usr/." %{{buildroot}}/usr/
%files
/usr/bin/firik-agent
/usr/share/applications/firik-agent.desktop
/usr/share/icons/hicolor/256x256/apps/firik-agent.png
""")
    run("rpmbuild", "--define", f"_topdir {top}", "--define", "__os_install_post %{nil}", "-bb", spec)
    for rpm in (top / "RPMS").rglob("*.rpm"):
        shutil.copy2(rpm, OUT / rpm.name)


def macos(arch):
    stage = ROOT / "build/dmg"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "dist/firik-agent.app", stage / "firik-agent.app", symlinks=True, dirs_exist_ok=True)
    (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
    run("hdiutil", "create", "-volname", "firik-agent", "-srcfolder", stage, "-ov", "-format", "UDZO",
        OUT / f"firik-agent-{VERSION}-macos-{arch}.dmg")
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", ROOT / "dist/firik-agent.app",
        OUT / f"firik-agent-{VERSION}-macos-{arch}.zip")


def windows():
    compiler = shutil.which("iscc") or r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    run(compiler, f"/DAppVersion={VERSION}", ROOT / "packaging/windows.iss")
    with zipfile.ZipFile(OUT / f"firik-agent-{VERSION}-windows-x64.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(ROOT / "dist/firik-agent.exe", "firik-agent.exe")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    if sys.platform == "darwin":
        macos(sys.argv[1])
    elif sys.platform == "win32":
        windows()
    else:
        linux()
