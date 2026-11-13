from pathlib import Path
import tarfile
import zipfile

import pytest

from scripts import package_hybrid_release, validate_release


def _write_versioned_project(root: Path, version: str = "1.2.3") -> None:
    (root / "pyproject.toml").write_text(f'[project]\nname = "firik-agent"\nversion = "{version}"\n', encoding="utf-8")
    (root / "package.json").write_text(f'{{"name": "firik-agent-workspace", "version": "{version}"}}\n', encoding="utf-8")
    (root / "apps" / "desktop").mkdir(parents=True, exist_ok=True)
    (root / "apps" / "desktop" / "package.json").write_text(
        f'{{"name": "@firik/desktop", "version": "{version}"}}\n', encoding="utf-8"
    )


def test_package_hybrid_release_windows_zip(tmp_path: Path) -> None:
    _write_versioned_project(tmp_path, "1.2.3")
    unpacked = tmp_path / "dist" / "hybrid-desktop" / "win-unpacked"
    (unpacked / "resources").mkdir(parents=True)
    (unpacked / "Firik Agent Hybrid.exe").write_bytes(b"mz-binary")
    (unpacked / "resources" / "app.asar").write_bytes(b"asar-data")

    archive = package_hybrid_release.package_hybrid_release("windows-x64", root=tmp_path)

    assert archive == tmp_path / "release_assets" / "firik-agent-hybrid-1.2.3-windows-x64.zip"
    with zipfile.ZipFile(archive) as zf:
        assert set(zf.namelist()) == {
            "Firik Agent Hybrid/Firik Agent Hybrid.exe",
            "Firik Agent Hybrid/resources/app.asar",
        }


def test_package_hybrid_release_linux_tar_gz_preserves_files_and_symlinks(tmp_path: Path) -> None:
    _write_versioned_project(tmp_path, "1.2.3")
    unpacked = tmp_path / "dist" / "hybrid-desktop" / "linux-unpacked"
    unpacked.mkdir(parents=True)
    binary = unpacked / "firik-agent-hybrid"
    binary.write_bytes(b"elf-binary")
    binary.chmod(0o755)
    (unpacked / "chrome-sandbox").symlink_to("firik-agent-hybrid")

    archive = package_hybrid_release.package_hybrid_release("linux-x86_64", root=tmp_path)

    assert archive == tmp_path / "release_assets" / "firik-agent-hybrid-1.2.3-linux-x86_64.tar.gz"
    with tarfile.open(archive, "r:gz") as tf:
        members = {member.name: member for member in tf.getmembers()}
        assert "Firik Agent Hybrid/firik-agent-hybrid" in members
        assert members["Firik Agent Hybrid/chrome-sandbox"].issym()
        assert members["Firik Agent Hybrid/chrome-sandbox"].linkname == "firik-agent-hybrid"


def test_package_hybrid_release_macos_uses_x64_fallback_and_ditto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_versioned_project(tmp_path, "1.2.3")
    bundle = tmp_path / "dist" / "hybrid-desktop" / "mac" / "Firik Agent Hybrid.app"
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_text("<plist/>", encoding="utf-8")

    recorded: list[list[str]] = []

    def fake_run(cmd: list[str], *, check: bool) -> None:
        recorded.append(cmd)
        assert check is True
        Path(cmd[-1]).write_bytes(b"zip-bytes")

    monkeypatch.setattr(package_hybrid_release.subprocess, "run", fake_run)

    archive = package_hybrid_release.package_hybrid_release("macos-x64", root=tmp_path)

    assert archive == tmp_path / "release_assets" / "firik-agent-hybrid-1.2.3-macos-x64.zip"
    assert recorded == [
        ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(bundle), str(archive)]
    ]


def test_package_hybrid_release_errors_when_unpacked_missing(tmp_path: Path) -> None:
    _write_versioned_project(tmp_path, "1.2.3")
    with pytest.raises(FileNotFoundError):
        package_hybrid_release.package_hybrid_release("macos-arm64", root=tmp_path)


def test_validate_release_checks_manifest_versions_and_hybrid_assets(tmp_path: Path) -> None:
    _write_versioned_project(tmp_path, "1.2.3")
    assets_dir = tmp_path / "release_assets"
    assets_dir.mkdir()
    for name in validate_release.expected_release_assets("1.2.3"):
        (assets_dir / name).write_bytes(f"asset:{name}".encode("utf-8"))

    assert any(name == "firik-agent-hybrid-1.2.3-linux-x86_64.tar.gz" for name in validate_release.expected_release_assets("1.2.3"))
    assert validate_release.validate_release(tmp_path, "v1.2.3", check_assets=True) == "Validated v1.2.3"
    checksums = (assets_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")
    assert "firik-agent-hybrid-1.2.3-macos-arm64.zip" in checksums

    (tmp_path / "apps" / "desktop" / "package.json").write_text('{"version": "0.0.1"}\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="apps/desktop/package.json"):
        validate_release.validate_release(tmp_path, "v1.2.3", check_assets=False)
