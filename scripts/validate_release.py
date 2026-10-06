"""Reject wrong tags or incomplete releases before publishing any assets."""
import hashlib
import os
from pathlib import Path
import re
import sys
import tomllib

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
tag = os.environ["RELEASE_TAG"]
if not re.fullmatch(r"v\d+\.\d+\.\d+", tag) or tag != f"v{version}":
    raise SystemExit(f"Tag {tag!r} does not match project version v{version}")
if "--assets" in sys.argv:
    assets = root / "release_assets"
    expected = [
        f"firik-agent-{version}-macos-arm64.dmg", f"firik-agent-{version}-macos-x64.dmg",
        f"firik-agent-{version}-macos-arm64.zip", f"firik-agent-{version}-macos-x64.zip",
        f"firik-agent-{version}-windows-x64-setup.exe", f"firik-agent-{version}-windows-x64.zip",
        f"firik-agent-{version}-linux-x86_64.tar.gz", f"firik-agent_{version}_amd64.deb",
        f"firik-agent-{version}-1.x86_64.rpm", f"firik_agent-{version}-py3-none-any.whl",
        f"firik_agent-{version}.tar.gz",
    ]
    for name in expected:
        path = assets / name
        if not path.is_file() or not path.stat().st_size:
            raise SystemExit(f"Required release asset missing or empty: {name}")
    lines = []
    for name in sorted(expected):
        digest = hashlib.file_digest((assets / name).open("rb"), "sha256").hexdigest()
        lines.append(f"{digest}  {name}\n")
    (assets / "SHA256SUMS.txt").write_text("".join(lines))
print(f"Validated {tag}")
