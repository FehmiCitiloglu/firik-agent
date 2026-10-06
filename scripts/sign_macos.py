"""Build, Developer ID sign, notarize, and verify a native macOS distribution.

Credentials stay in the Keychain; this script never accepts a password.
"""
import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "dist/firik-agent.app"
OUT = ROOT / "release_assets"
LOGS = ROOT / "build/notarization"


def notary_credentials(profile):
    args = ["--keychain-profile", profile]
    if keychain := os.environ.get("FIRIK_NOTARY_KEYCHAIN"):
        args.extend(["--keychain", keychain])
    return args


def run(*args, capture=False, env=None):
    return subprocess.run(
        [str(arg) for arg in args], cwd=ROOT, check=True, env=env,
        capture_output=capture, text=True,
    )


def notarize(path, profile):
    result = run(
        "xcrun", "notarytool", "submit", path, *notary_credentials(profile),
        "--wait", "--timeout", "30m", "--output-format", "json", capture=True,
    )
    report = json.loads(result.stdout)
    (LOGS / f"{path.name}.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"{path.name}: {report.get('status')} ({report.get('id')})", flush=True)
    if report.get("status") != "Accepted":
        if report.get("id"):
            log = run(
                "xcrun", "notarytool", "log", report["id"],
                *notary_credentials(profile), capture=True,
            )
            (LOGS / f"{path.name}.issues.json").write_text(log.stdout)
        raise RuntimeError(f"Apple did not accept {path.name}; see {LOGS}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True, help="Developer ID Application name or SHA-1")
    parser.add_argument("--profile", required=True, help="Existing notarytool Keychain profile")
    parser.add_argument("--skip-build", action="store_true", help="Use an already signed, current dist/firik-agent.app")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This script requires macOS")
    identities = run("security", "find-identity", "-v", "-p", "codesigning", capture=True).stdout
    matches = re.findall(r'([0-9A-F]{40}) "(Developer ID Application:[^\"]+)"', identities)
    selected = [sha for sha, name in matches if args.identity in (sha, name)]
    if len(selected) != 1:
        parser.error("Choose an installed, valid Developer ID Application identity with its private key")
    identity = selected[0]
    # Check credentials before performing the build. No passwords are printed.
    run("xcrun", "notarytool", "history", *notary_credentials(args.profile), capture=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    if not args.skip_build:
        env = dict(os.environ, FIRIK_CODESIGN_IDENTITY=identity,
                   PYINSTALLER_STRICT_BUNDLE_CODESIGN_ERROR="1")
        run(sys.executable, "-m", "PyInstaller", "firik-agent.spec", "--clean", "--noconfirm", env=env)
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", APP)
    details = subprocess.run(
        ["codesign", "-dvvv", str(APP)], check=True, capture_output=True, text=True,
    ).stderr
    if "Authority=Developer ID Application:" not in details or "runtime" not in details:
        raise RuntimeError("The app must have a Developer ID signature and hardened runtime")
    with (APP / "Contents/Info.plist").open("rb") as source:
        app_version = plistlib.load(source)["CFBundleShortVersionString"]
    if app_version != version("firik-agent"):
        raise RuntimeError("The app version does not match the checkout")
    architecture = run("lipo", "-archs", APP / "Contents/MacOS/firik-agent", capture=True).stdout.strip()
    arch = {"arm64": "arm64", "x86_64": "x64"}.get(architecture)
    if not arch:
        raise RuntimeError(f"Unsupported release architecture: {architecture}")
    run(sys.executable, "scripts/smoke_desktop.py")
    upload = LOGS / f"firik-agent-{app_version}-{arch}-notary.zip"
    run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", APP, upload)
    notarize(upload, args.profile)
    run("xcrun", "stapler", "staple", APP)
    run("xcrun", "stapler", "validate", APP)
    # Package after stapling so the ZIP and DMG both contain the offline ticket.
    run(sys.executable, "scripts/package_desktop.py", arch)
    dmg = OUT / f"firik-agent-{app_version}-macos-{arch}.dmg"
    run("codesign", "--force", "--timestamp", "--sign", identity, dmg)
    notarize(dmg, args.profile)
    run("xcrun", "stapler", "staple", dmg)
    run("xcrun", "stapler", "validate", dmg)
    run("codesign", "--verify", "--deep", "--strict", "--verbose=2", APP)
    run("spctl", "--assess", "--type", "execute", "--verbose=2", APP)
    run("spctl", "--assess", "--type", "open", "--context", "context:primary-signature", "--verbose=2", dmg)
    files = [dmg, OUT / f"firik-agent-{app_version}-macos-{arch}.zip"]
    checksums = []
    for path in files:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        checksums.append(f"{digest.hexdigest()}  {path.name}")
    (OUT / f"SHA256SUMS-macos-{arch}.txt").write_text("\n".join(checksums) + "\n")
    print(f"Verified signed and notarized macOS {arch} installers: {OUT}")


if __name__ == "__main__":
    main()
