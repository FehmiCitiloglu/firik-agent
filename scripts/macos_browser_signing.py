"""Preserve the Playwright driver's JIT permission after PyInstaller signing.

The browser cache stays outside the app. Only the bundled Node driver executes
JIT code here; the Python app and its other nested binaries do not need it.
"""
from pathlib import Path
import plistlib
import subprocess


ROOT = Path(__file__).resolve().parents[1]
NODE_ENTITLEMENTS = ROOT / "packaging/macos-playwright-node.entitlements"
APP_ENTITLEMENTS = ROOT / "firik_agent/ui/assets/macos-voice.entitlements"
_NODE_RELATIVE_PATH = Path("Contents/Frameworks/playwright/driver/node")
_JIT_ENTITLEMENTS = {"com.apple.security.cs.allow-jit": True}


def playwright_node(app, *, required=False):
    """Select only the known packaged driver, never an arbitrary `node` file."""
    app = Path(app).resolve()
    node = app / _NODE_RELATIVE_PATH
    if not node.exists():
        if required:
            raise FileNotFoundError(f"Packaged Playwright Node is missing: {node}")
        return None
    resolved = node.resolve()
    if not resolved.is_relative_to(app) or not resolved.is_file():
        raise ValueError("Packaged Playwright Node must remain inside its app bundle")
    return resolved


def verify_playwright_node_entitlements(app, *, required=False):
    """Reject a browser-enabled bundle whose driver lost its JIT permission."""
    node = playwright_node(app, required=required)
    if node is None:
        return False
    result = subprocess.run(
        # Current codesign defaults to a human-readable dictionary here.
        # Request XML explicitly rather than relying on the deprecated `:-`.
        ["/usr/bin/codesign", "--display", "--entitlements", "-", "--xml", str(node)],
        check=True, capture_output=True,
    )
    try:
        entitlements = plistlib.loads(result.stdout)
    except (plistlib.InvalidFileException, ValueError) as error:
        raise RuntimeError("Packaged Playwright Node has no valid JIT entitlements") from error
    if entitlements != _JIT_ENTITLEMENTS:
        raise RuntimeError("Packaged Playwright Node must have only its allow-jit entitlement")
    return True


def sign_playwright_node(app, identity=None, *, required=False):
    """Sign the driver, then its containing bundle without rewriting children.

    Run after BUNDLE: PyInstaller's deep bundle signing replaces the original
    vendor Node signature, so signing the driver earlier would be ineffective.
    """
    app = Path(app).resolve()
    node = playwright_node(app, required=required)
    if node is None:
        return False
    with NODE_ENTITLEMENTS.open("rb") as stream:
        if plistlib.load(stream) != _JIT_ENTITLEMENTS:
            raise ValueError("Playwright Node entitlements must grant only allow-jit")
    identity = identity or "-"
    common = ["/usr/bin/codesign", "--force", "--all-architectures", "--sign", identity,
              "--timestamp" if identity != "-" else "--timestamp=none"]
    subprocess.run([*common, "--options=runtime", "--entitlements", str(NODE_ENTITLEMENTS), str(node)],
                   check=True)
    # `--deep` here would replace Node's new entitlement with the app's plist.
    app_options = ["--options=runtime"] if identity != "-" else []
    subprocess.run([*common, *app_options, "--entitlements", str(APP_ENTITLEMENTS), str(app)], check=True)
    verify_playwright_node_entitlements(app, required=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    return True
