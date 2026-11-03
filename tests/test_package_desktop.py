import subprocess
import tarfile
from unittest.mock import Mock

import pytest

from scripts import package_desktop


def test_dmg_retries_resource_busy_and_removes_partial_file(tmp_path, monkeypatch):
    destination = tmp_path / "installer.dmg"
    destination.write_bytes(b"partial")
    run = Mock(side_effect=[subprocess.CompletedProcess([], 1, "", "Resource busy"),
                            subprocess.CompletedProcess([], 0, "", "")])
    monkeypatch.setattr(package_desktop.subprocess, "run", run)
    monkeypatch.setattr(package_desktop.time, "sleep", lambda _: None)
    package_desktop.create_dmg(tmp_path, destination)
    assert run.call_count == 2
    assert not destination.exists()


def test_dmg_does_not_retry_other_errors(tmp_path, monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 1, "", "permission denied"))
    monkeypatch.setattr(package_desktop.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        package_desktop.create_dmg(tmp_path, tmp_path / "installer.dmg")
    assert run.call_count == 1


def test_linux_manifests_recommend_optional_local_speech_without_requiring_it(tmp_path, monkeypatch):
    """Generate both actual manifests while avoiding native package-manager calls."""
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/firik-agent").write_bytes(b"frozen-desktop")
    assets = tmp_path / "firik_agent/ui/assets"
    assets.mkdir(parents=True)
    (assets / "icon.png").write_bytes(b"test-icon")
    (tmp_path / "firik-agent.desktop").write_text("[Desktop Entry]\nExec=firik-gui\n")
    output = tmp_path / "release_assets"
    output.mkdir()
    monkeypatch.setattr(package_desktop, "ROOT", tmp_path)
    monkeypatch.setattr(package_desktop, "OUT", output)
    commands = []
    monkeypatch.setattr(package_desktop, "run", lambda *args: commands.append(args))

    package_desktop.linux()

    debian = (tmp_path / "build/linux-package/DEBIAN/control").read_text()
    rpm = (tmp_path / "build/rpm/SPECS/firik-agent.spec").read_text()
    fields = lambda text: dict(line.split(":", 1) for line in text.splitlines()
                               if ":" in line and not line.startswith(" "))
    debian_fields, rpm_fields = fields(debian), fields(rpm)
    assert "espeak-ng" in debian_fields["Recommends"].split(", ")
    assert rpm_fields["Recommends"].strip() == "espeak-ng"
    assert "espeak" not in debian_fields["Depends"]
    assert "espeak" not in rpm_fields["Requires"]
    assert commands[0][0] == "dpkg-deb"
    assert commands[1][0] == "rpmbuild"
    with tarfile.open(next(output.glob("*.tar.gz")), "r:gz") as archive:
        assert archive.extractfile("usr/bin/firik-agent").read() == b"frozen-desktop"
