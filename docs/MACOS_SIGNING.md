# macOS Developer ID signing and notarization

GitHub/DMG distribution uses **Developer ID Application**, with its private key
installed in this Mac's Keychain. An Apple Development certificate is insufficient.
Check available identities with `security find-identity -v -p codesigning`.
In Xcode → Settings → Apple Accounts → team → Manage Certificates, create or
import the Developer ID Application identity. Existing certificates without their
private keys cannot sign a build.

Store notarization credentials interactively in Keychain (never in Git or chat):

```sh
xcrun notarytool store-credentials firik-notary
```

The prompts request the Apple ID, Developer Team ID, and an **app-specific
password** generated at <https://account.apple.com>. The Team ID is the developer
team's ID, not the Apple Development certificate's personal ID. An existing
App Store Connect API key can also be used with notarytool. Do not create extra
entitlements unless the app demonstrably needs them.

With the project's Python environment and PyInstaller installed:

```sh
.venv/bin/python scripts/sign_macos.py \
  --identity 'Developer ID Application: YOUR NAME (TEAM_ID)' \
  --profile firik-notary
```

The script signs all bundled code using PyInstaller, enables hardened runtime,
smoke tests the app, submits it to Apple, and requires `Accepted`. It staples the
app before creating the ZIP/DMG, signs and notarizes the DMG, then validates both
stapled tickets and Gatekeeper acceptance. Reports are under `build/notarization`;
installers and architecture-specific checksums are under `release_assets`.
Failed notarization must be resolved before distributing those files.

## GitHub Releases

The Desktop Release workflow now requires signed and notarized macOS installers
for both arm64 and Intel x64. Add these repository secrets at Settings → Secrets
and variables → Actions → New repository secret:

| Secret | Value |
| --- | --- |
| `MACOS_CERTIFICATE_BASE64` | Base64 of an exported Developer ID Application `.p12`, including the private key |
| `MACOS_CERTIFICATE_PASSWORD` | Password chosen when exporting that `.p12` |
| `APPLE_ID` | Apple Account email with access to this developer team |
| `APPLE_TEAM_ID` | Developer Program Team ID matching the certificate |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password generated at account.apple.com for notarization |

The password in `APPLE_APP_SPECIFIC_PASSWORD` is not your normal Apple Account
password. The `.cer` file alone is insufficient; export the certificate and
private key as a password-protected `.p12` from Xcode Manage Certificates
(Control-click → Export Certificate) or Keychain Access → My Certificates.
To prepare its secret value without printing it to terminal logs:

```sh
base64 -i /absolute/path/DeveloperIDApplication.p12 | pbcopy
```

Paste the clipboard contents directly into `MACOS_CERTIFICATE_BASE64`. Base64
is an encoding, not encryption; keep it in a secret. Never commit the P12 or
passwords. A separate GitHub Environment is optional: the current workflow uses
repository secrets and does not declare an `environment`. If you choose
environment secrets instead, you must configure the macOS jobs to reference
that environment. No extra signing identity or keychain password secret is
needed: the runner selects the certificate's SHA-1 identity and generates its
own disposable keychain password. This app's existing capabilities do not need
a provisioning profile for Developer ID distribution.

Each native macOS runner imports the P12 into a temporary Keychain, validates
the notarization login and certificate's Team ID, builds and signs all bundled
code, smoke tests it, notarizes and staples the app, then packages and notarizes
the DMG. Gatekeeper and ticket validation must pass before installer upload.
Missing secrets or signing/notarization failures stop the release; there is no
unsigned fallback. Credentials are removed in an `always()` cleanup step.
Notarization JSON reports are separate workflow artifacts, not release assets.
The same five secrets work for both Mac architectures.

To validate the imported certificate, private key, matching Team ID and actual
Apple notarization login without building or publishing a release, manually run
Desktop Release with `signing_check_only` enabled. The tag input is unused in
this mode. This check uses the selected workflow ref's source, then removes its
temporary Keychain. Normal release runs still check out the requested tag.

Commit and push the workflow, scripts, and spec changes before creating the next
version tag. Manual reruns still check out the requested tag: rerunning an old
tag will use its old source and packaging scripts. Existing published releases
are not re-signed by these local edits. Live signing and Apple acceptance remain
unverified until the credentials are configured and the workflow runs.

Each architecture requires a matching native Python build environment for a
local build. GitHub's matrix already provides both environments.

References: [Apple Developer ID](https://developer.apple.com/developer-id/),
[Apple notarization workflow](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow),
[PyInstaller signing](https://pyinstaller.org/en/stable/feature-notes.html#macos-binary-code-signing).
