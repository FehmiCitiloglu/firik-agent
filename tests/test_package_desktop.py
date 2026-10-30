import subprocess
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
