"""Reject wrong tags or incomplete releases before publishing any assets."""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def expected_release_assets(version: str) -> list[str]:
    """Return all required legacy, hybrid desktop, and Python distribution filenames."""
    return [
        f"firik-agent-{version}-macos-arm64.dmg",
        f"firik-agent-{version}-macos-x64.dmg",
        f"firik-agent-{version}-macos-arm64.zip",
        f"firik-agent-{version}-macos-x64.zip",
        f"firik-agent-{version}-windows-x64-setup.exe",
        f"firik-agent-{version}-windows-x64.zip",
        f"firik-agent-{version}-linux-x86_64.tar.gz",
        f"firik-agent_{version}_amd64.deb",
        f"firik-agent-{version}-1.x86_64.rpm",
        f"firik-agent-hybrid-{version}-macos-arm64.zip",
        f"firik-agent-hybrid-{version}-macos-x64.zip",
        f"firik-agent-hybrid-{version}-windows-x64.zip",
        f"firik-agent-hybrid-{version}-linux-x86_64.tar.gz",
        f"firik_agent-{version}-py3-none-any.whl",
        f"firik_agent-{version}.tar.gz",
    ]


def validate_manifest_versions(root: Path, version: str) -> None:
    """Ensure JS/Electron package manifests match the Python package version."""
    for rel_path in ("package.json", "apps/desktop/package.json"):
        manifest_path = root / rel_path
        manifest_version = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
        if manifest_version != version:
            raise SystemExit(
                f"{rel_path} version {manifest_version!r} does not match pyproject.toml version {version!r}"
            )


def validate_release(root: Path, release_tag: str, *, check_assets: bool = False) -> str:
    """Validate tag alignment, manifest versions, and optionally built release assets."""
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if not re.fullmatch(r"v\d+\.\d+\.\d+", release_tag) or release_tag != f"v{version}":
        raise SystemExit(f"Tag {release_tag!r} does not match project version v{version}")
    validate_manifest_versions(root, version)
    if check_assets:
        assets = root / "release_assets"
        expected = expected_release_assets(version)
        for name in expected:
            path = assets / name
            if not path.is_file() or not path.stat().st_size:
                raise SystemExit(f"Required release asset missing or empty: {name}")
        lines = []
        for name in sorted(expected):
            with (assets / name).open("rb") as fh:
                digest = hashlib.file_digest(fh, "sha256").hexdigest()
            lines.append(f"{digest}  {name}\n")
        (assets / "SHA256SUMS.txt").write_text("".join(lines), encoding="utf-8")
    return f"Validated {release_tag}"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint used by GitHub Actions release validation."""
    args = argv if argv is not None else sys.argv[1:]
    message = validate_release(ROOT, os.environ["RELEASE_TAG"], check_assets="--assets" in args)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
