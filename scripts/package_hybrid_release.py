"""Archive unpacked Electron hybrid desktop builds into distributable release assets."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP_BUNDLE_NAME = "Firik Agent Hybrid.app"
PORTABLE_DIR_NAME = "Firik Agent Hybrid"


@dataclass(frozen=True)
class PlatformSpec:
    """Packaging specification for a target hybrid desktop platform."""

    archive_suffix: str
    candidates: tuple[str, ...]
    archive_kind: str


PLATFORM_SPECS: dict[str, PlatformSpec] = {
    "macos-arm64": PlatformSpec(
        archive_suffix="macos-arm64.zip",
        candidates=(f"mac-arm64/{APP_BUNDLE_NAME}", f"mac/{APP_BUNDLE_NAME}"),
        archive_kind="ditto-zip",
    ),
    "macos-x64": PlatformSpec(
        archive_suffix="macos-x64.zip",
        candidates=(f"mac-x64/{APP_BUNDLE_NAME}", f"mac/{APP_BUNDLE_NAME}"),
        archive_kind="ditto-zip",
    ),
    "windows-x64": PlatformSpec(
        archive_suffix="windows-x64.zip",
        candidates=("win-unpacked",),
        archive_kind="dir-zip",
    ),
    "linux-x86_64": PlatformSpec(
        archive_suffix="linux-x86_64.tar.gz",
        candidates=("linux-unpacked",),
        archive_kind="dir-tar-gz",
    ),
}


def read_project_version(root: Path = ROOT) -> str:
    """Return the package version declared in pyproject.toml."""
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        if not match:
            raise RuntimeError("Could not find project version in pyproject.toml")
        return match.group(1)
    return str(tomllib.loads(text)["project"]["version"])


def resolve_unpacked_source(dist_dir: Path, spec: PlatformSpec) -> Path:
    """Find the existing unpacked bundle or directory for a platform."""
    checked: list[Path] = []
    for rel in spec.candidates:
        candidate = dist_dir / rel
        checked.append(candidate)
        if candidate.exists():
            return candidate
    joined = ", ".join(str(path) for path in checked)
    raise FileNotFoundError(f"Unpacked hybrid desktop build not found; checked: {joined}")


def _write_directory_zip(source_dir: Path, archive_path: Path) -> None:
    """Zip an unpacked directory beneath a stable top-level application folder."""
    files = sorted(path for path in source_dir.rglob("*") if path.is_file() or path.is_symlink())
    if not files:
        raise FileNotFoundError(f"No files found inside {source_dir}")
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            rel = path.relative_to(source_dir).as_posix()
            zf.write(path, f"{PORTABLE_DIR_NAME}/{rel}")


def _write_directory_tar_gz(source_dir: Path, archive_path: Path) -> None:
    """Create a gzip-compressed tar archive preserving symlinks and executable modes."""
    if not any(source_dir.iterdir()):
        raise FileNotFoundError(f"No files found inside {source_dir}")
    with tarfile.open(archive_path, "w:gz") as tf:
        tf.add(source_dir, arcname=PORTABLE_DIR_NAME)


def _write_ditto_zip(app_bundle: Path, archive_path: Path) -> None:
    """Create a macOS zip archive preserving bundle symlinks and metadata."""
    subprocess.run(
        ["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app_bundle), str(archive_path)],
        check=True,
    )


def package_hybrid_release(
    platform_name: str,
    *,
    root: Path = ROOT,
    dist_dir: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    """Archive an unpacked hybrid Electron build for the specified platform."""
    if platform_name not in PLATFORM_SPECS:
        supported = ", ".join(sorted(PLATFORM_SPECS))
        raise ValueError(f"Unsupported hybrid release platform {platform_name!r}; expected one of: {supported}")

    spec = PLATFORM_SPECS[platform_name]
    version = read_project_version(root)
    resolved_dist = dist_dir if dist_dir is not None else root / "dist" / "hybrid-desktop"
    resolved_out = out_dir if out_dir is not None else root / "release_assets"
    resolved_out.mkdir(parents=True, exist_ok=True)

    source = resolve_unpacked_source(resolved_dist, spec)
    archive_path = resolved_out / f"firik-agent-hybrid-{version}-{spec.archive_suffix}"
    if archive_path.exists():
        archive_path.unlink()

    if spec.archive_kind == "ditto-zip":
        _write_ditto_zip(source, archive_path)
    elif spec.archive_kind == "dir-zip":
        _write_directory_zip(source, archive_path)
    elif spec.archive_kind == "dir-tar-gz":
        _write_directory_tar_gz(source, archive_path)
    else:  # pragma: no cover - guarded by PLATFORM_SPECS
        raise AssertionError(f"Unhandled archive_kind {spec.archive_kind!r}")

    if not archive_path.is_file() or archive_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to create non-empty archive at {archive_path}")
    return archive_path


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for packaging hybrid desktop release archives."""
    parser = argparse.ArgumentParser(description="Package unpacked hybrid desktop builds for release.")
    parser.add_argument("platform", choices=sorted(PLATFORM_SPECS), help="Target release platform identifier")
    parser.add_argument("--dist-dir", type=Path, default=None, help="Directory containing unpacked electron-builder output")
    parser.add_argument("--out-dir", type=Path, default=None, help="Output directory for packaged archives")
    args = parser.parse_args(argv)

    archive_path = package_hybrid_release(args.platform, dist_dir=args.dist_dir, out_dir=args.out_dir)
    print(archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
