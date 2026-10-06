"""Import GitHub Actions signing secrets into a disposable runner Keychain."""
import base64
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys

REQUIRED = (
    "MACOS_CERTIFICATE_BASE64", "MACOS_CERTIFICATE_PASSWORD",
    "APPLE_ID", "APPLE_TEAM_ID", "APPLE_APP_SPECIFIC_PASSWORD",
)


def run(*args):
    # Never include credential-bearing command lines in exception messages.
    result = subprocess.run([str(arg) for arg in args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode}); check the signing secrets")
    return result.stdout


def main():
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        raise SystemExit("Missing GitHub Actions secrets: " + ", ".join(missing))
    if sys.platform != "darwin" or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Only run this script on a macOS GitHub Actions runner")
    temporary = Path(os.environ["RUNNER_TEMP"])
    keychain = temporary / "firik-signing.keychain-db"
    certificate = temporary / "firik-signing.p12"
    password = secrets.token_urlsafe(32)
    original_keychains = re.findall(r'"([^\"]+)"', run("security", "list-keychains", "-d", "user"))
    try:
        with certificate.open("wb") as destination:
            certificate.chmod(0o600)
            destination.write(base64.b64decode("".join(os.environ[REQUIRED[0]].split()), validate=True))
        run("security", "create-keychain", "-p", password, keychain)
        run("security", "set-keychain-settings", "-lut", "21600", keychain)
        run("security", "unlock-keychain", "-p", password, keychain)
        run("security", "import", certificate, "-P", os.environ[REQUIRED[1]],
            "-k", keychain, "-T", "/usr/bin/codesign", "-T", "/usr/bin/security")
        run("security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:",
            "-s", "-k", password, keychain)
        run("security", "list-keychains", "-d", "user", "-s", keychain, *original_keychains)
        identities = run("security", "find-identity", "-v", "-p", "codesigning", keychain)
        matches = re.findall(r'([0-9A-F]{40}) "(Developer ID Application:[^\"]+)"', identities)
        if len(matches) != 1:
            raise RuntimeError("The P12 must contain exactly one valid Developer ID Application identity and its private key")
        identity, name = matches[0]
        if not name.endswith(f"({os.environ['APPLE_TEAM_ID']})"):
            raise RuntimeError("APPLE_TEAM_ID does not match the Developer ID Application certificate")
        run("xcrun", "notarytool", "store-credentials", "firik-notary",
            "--apple-id", os.environ["APPLE_ID"], "--team-id", os.environ["APPLE_TEAM_ID"],
            "--password", os.environ["APPLE_APP_SPECIFIC_PASSWORD"], "--keychain", keychain)
        with Path(os.environ["GITHUB_ENV"]).open("a") as environment:
            environment.write(f"FIRIK_CODESIGN_IDENTITY={identity}\n")
            environment.write(f"FIRIK_NOTARY_KEYCHAIN={keychain}\n")
        print("Developer ID certificate and validated notarization credentials are ready")
    except Exception:
        if keychain.exists():
            subprocess.run(["security", "delete-keychain", str(keychain)], capture_output=True)
        raise
    finally:
        certificate.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(str(error)) from None
