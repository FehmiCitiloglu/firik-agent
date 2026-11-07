# Inline Browser verification — 7 November 2026

The later [public Web permission follow-up](BROWSER_PUBLIC_WEB_PERMISSIONS_20261107.md)
removes repeated approval for genuine builtin Web search/reading after browser use.

The reported screenshot showed `permission_denied` after enabling the browser
checkbox. Start/navigation previously depended on separately applying that UI
state. The new panel applies the grant before starting and navigating. It lives
in a resizable pane beside chat, uses the same managed Chromium page and starts
headlessly, without a second Chromium window.

The address bar accepts a domain, HTTPS URL or search. Back/forward/reload,
tabs, close, manual control and return to the agent are available in the panel.
The initial viewport matches the visible pane, rather than shrinking a fixed
desktop page into a narrow preview. Narrow layouts wrap the chat toolbar and
temporarily collapse conversation history to leave room for both panes.

Desktop browsing no longer needs a domain allowlist. Public HTTPS destinations
and resources pass the existing DNS/address checks; public HTTP, private network
targets, URL credentials and unsupported schemes remain blocked. Exact host-owned
local development exceptions are retained. CLI and delegated grants can remain
narrower than a desktop grant.

Manual click/keyboard/scroll input and preview are trusted host methods, absent
from model tool definitions. They use the same owned page and locks, check live
permissions, and do not consume the agent's action budget. During manual control,
agent observations, screenshots and page metadata remain unavailable. Host frames
stay in memory and mask sensitive fields. A new read-only observation is attempted
on resume; a continuously changing page can request a fresh observation without
falsely reporting that control transfer failed.

Previously every HTTP redirect was blocked. Redirects now validate each destination
with a ten-hop bound. Top-level redirects preserve the browser's real final URL;
POST 302/303 follows with GET without replaying the body. Mutation-preserving
redirects and cross-origin subresource redirects remain blocked. The latter avoids
forwarding Authorization or bypassing Chromium's CORS rules through response
fulfillment. Failed contexts and disconnected Chromium engines can be explicitly
restarted, without silently replaying a website action.

## Verification

- [Filtered regression](Evidence/Browser/20261107/regression.log): 2,244 passed,
  25 skipped, 454 deselected. This excludes native UI, live and Docker cases.
- [Focused browser regression](Evidence/Browser/20261107/focused.log): 180 passed.
- [Real Chromium, coding and development servers](Evidence/Browser/20261107/chromium.log):
  37 passed. Tests include validated redirects, unsafe redirect rejection,
  same-page manual input and retained ownership boundaries.
- [Public HTTPS acceptance](Evidence/Browser/20261107/public-https.json): empty
  origin list; Example Domain, redirected Playwright homepage and LinkedIn's public
  login page returned HTTP 200. Host frames, manual observation denial, public HTTP
  rejection, host resume and cleanup were checked. No login or provider request.
  [Reproduction](Evidence/Browser/20261107/verify_public_browser.py).
- [Native UI log](Evidence/Browser/20261107/native-ui.log): 38 native Tk cases
  passed, including browser input, conversation navigation and the 940/1120/1700
  pixel window layouts.
- [Build](Evidence/Browser/20261107/build.log) and
  [frozen smoke](Evidence/Browser/20261107/frozen-smoke.log) record the local macOS
  arm64 bundle, disposable UI/PortAudio probe and bundled-driver Chromium launch.
- [Installation receipt](Evidence/Browser/20261107/installation.json): the verified
  0.2.48 bundle replaced `/Applications/firik-agent.app` with a rollback copy
  retained under `/private/tmp`. Strict nested signature verification passed.
  The installed app was relaunched and its restored conversation was visually
  inspected. Native coordinate automation did not activate Tk controls in that
  inspection; same-window browsing/input is established by the native tests and
  real Chromium acceptance above, rather than a claimed manual packaged-site test.

## Remaining boundaries

This is an interactive Chromium frame surface inside Tk, rather than a native
embedded webview. Persistent login profiles, file transfer, downloads, WebSockets
and service workers remain unavailable. Cross-origin subresource redirects can
also prevent some site features from loading. LinkedIn account authentication,
CAPTCHA/MFA, live provider browser usage and native Windows/Linux/Intel macOS
were not exercised by these checks. The macOS artifact uses local ad hoc signing;
this work does not publish or notarize a release.
