# Using the optional native Browser

Browser capability starts disabled. It runs a managed ephemeral Chromium context
under the host user's privileges and uses Firik's selected provider and existing
native agent loop. It never imports your regular browser profile or grants
browser access merely because Web MCP is enabled.

## Installation and diagnostics

From a Python source installation:

```sh
python -m pip install -e '.[browser]'
firik-agent browser install
firik-agent browser doctor
firik-agent browser doctor --launch
```

The `browser` extra supplies Playwright. `browser install` explicitly installs
the Chromium version matching that Playwright package. `doctor` inspects the
package and executable without downloading or launching Chromium; `--launch`
explicitly probes a real launch. Use `--launch --headful` to test a graphical
session. A frozen desktop app cannot run `python -m playwright` as itself; use a
matching source installation for the external browser distribution. Frozen builds
use the normal external Playwright cache, preserving an explicit
`PLAYWRIGHT_BROWSERS_PATH` override. The local arm64 macOS bundle has passed a
real browser launch probe; other native platforms still need equivalent checks.

On Linux, missing browser system libraries or a missing graphical display can
prevent launch. Headless mode does not require a display. Playwright manages its
own Chromium; OS-wide Accessibility permission is unnecessary.

## Enablement and network policy

The validated `browser` configuration uses flat keys:

```yaml
browser:
  enabled: false
  headless: true
  strict_network_isolation: false
  max_sessions: 3
  max_tabs_per_session: 8
  max_actions_per_task: 100
  action_timeout_ms: 15000
  navigation_timeout_ms: 30000
  max_snapshot_chars: 16000
```

Global enablement makes the service available; a separate explicit conversation
or project grant is still required. Existing sessions and projects do not gain
browser authority after an upgrade. Desktop grants allow public HTTPS websites
and their public HTTPS resources without a domain list. Public HTTP destinations,
embedded credentials, unsupported schemes and private network addresses are
rejected. CLI grants can remain restricted to exact origins. Local development
requires an explicit private-origin grant as well as ordinary origin admission;
this is the exception for an authorized local HTTP server. Page content cannot
change these host-owned permissions.

The host Browser workspace supplies the current identity and grant. Agent tool
arguments cannot change these. Screenshots are separately opt-in, and model
vision delivery remains disabled in the native browser adapter.

## Desktop controls

In a conversation, open **Tarayıcı** beside the MCP selector. The panel opens
alongside the chat in the same window. Enter a website address and press Enter
or use **Git**; the panel applies its conversation permission before starting
Chromium. Public HTTPS sites need no origin entries. The panel keeps its live
page in memory and exposes familiar navigation and tab controls. Chat grants
last for the current app session; restoring a chat does not restore browser
authority. Enabling one scope also activates the service in memory, without
granting other conversations access.
Firik's connected builtin Web search/reader automatically applies the public
HTTPS policy, including DNS/private-address and redirect checks, even after a
chat has used Browser. No domain list or per-site approval is needed. This
exception is bound to the host-registered in-process implementation; another MCP
cannot obtain it by calling itself "web". Explicit MCP deny/confirmation settings
still apply. Public browsing in coding also defaults to navigation without a
site-by-site prompt when its public HTTPS grant is enabled; explicit policy and
plan-mode restrictions still apply.

Once a chat enables Browser or contains a browser observation, unrelated
host, plugin, meta or MCP tools require a separate exact approval through the
existing host dialog. Without a trusted approval callback those tools are hidden
and execution is denied. This protection survives Browser disablement, chat
restoration and compaction because page instructions remain untrusted in history;
explicitly clearing that history removes it when Browser is disabled. CLI Browser
tasks use the same rule and reject unavailable non-interactive approvals.

In a project, open the **Tarayıcı** Coding Agent workspace tab. Apply project
Browser permission before starting a run. Project grants are
saved with coding preferences. The project Browser toolbar and preview operate
only during an active coding run, through its existing policy and execution
journal. Contexts close when that run ends. Start development servers separately
through the authorized command workflow; the Browser panel does not start one.

The desktop panel uses headless managed Chromium, so a separate Chromium window
is unnecessary. It displays an interactive image of the same page the agent
controls, rather than a second browser session. **Kontrolü Al** pauses the agent
and permits clicks, scrolling and typing through the panel. **Ajana Geri Ver**
resumes automation with fresh observations. These explicit host actions do not
ask the user to approve their own click a second time. Agent interactions still
use the existing host approval flow. Live preview updates only while visible,
skips unchanged frames and masks sensitive input controls. Agent observations
remain blocked throughout manual control. Stop revokes that owner's Browser
permission and closes its context. Previews remain in memory and do not
automatically become model vision input or persisted evidence.

Desktop installers include the Playwright driver. Chromium remains a separate,
explicit installation in the user's external cache. With Python available, use
`python -m pip install 'playwright>=1.64.1,<1.65'` followed by
`python -m playwright install chromium`, under the same OS user as the desktop
app. Linux also needs Chromium's system libraries (`python -m playwright install
--with-deps chromium`). Release builders install Chromium only to validate the
packaged driver; browser binaries are not included in the installers.

## CLI sessions and native agent tasks

These examples assume a development server you already started through an
authorized command workflow:

```sh
firik-agent browser start --origin http://127.0.0.1:3000 \
  --allow-private-origin http://127.0.0.1:3000 --url http://127.0.0.1:3000

firik-agent browser run --origin http://127.0.0.1:3000 \
  --allow-private-origin http://127.0.0.1:3000 \
  --prompt 'Open the frontend and inspect browser console errors.'
```

`start` runs an owned operator session with status, sessions, snapshot,
`navigate URL`, handoff, resume and close commands. Its resources close on exit.
There is no detached browser daemon and no fabricated cross-process status.
`run` uses `Agent.run_stream` and the selected native provider. It can incur the
provider's normal usage charges. `--provider`, `--model` and `--config` select
existing configured providers; they do not create a new inference framework.

Use `--headful` for a visible managed window and manual authentication. Use
`--screenshots` only when you want screenshot artifacts. Non-interactive runs
reject an operation that needs unavailable approval; they do not approve it.

## Supervised local development servers

Coding Agent also exposes `start_dev_server`, `inspect_dev_server` and
`stop_dev_server`. Inspect the project's existing scripts, configure an explicit
host-owned loopback origin/port grant, then request a foreground command
such as the project's existing development script. Start uses the current
`CodingRunControl` shell, network and browser-navigation approvals. It waits for
an HTTP readiness response while the owned command is alive. An existing exact
command/origin can be reused; an occupied unrelated port is rejected.

The supervisor uses a dedicated workspace on Firik's existing host command
backend, so file tools and ordinary test commands can continue while it runs.
Lifetime, readiness timeout and logs are bounded. Run cancellation, origin
revocation, `stop_dev_server` and run cleanup stop its process group. Arbitrary
shell backgrounding is rejected. Choose the port explicitly; this does not
automatically grant dynamically discovered ports. Docker server publishing is
not implemented and fails closed without host fallback. No development package
is installed automatically.

After edits, navigate or reload the application and call `browser_verify` with
an explicit assertion before linking its evidence to `record_verification`.
The verification receipt must match the current source digest and page; an old
open tab or an empty browser cannot certify new source changes.

## Review, handoff and cancellation

Review the exact destination, target, action and prepared form state in the host
approval flow. Treat clicks, key presses and form entry as state-changing
operations. Rejection prevents dispatch. An expired or changed prepared target
requires a fresh observation and approval.

For login, use manual handoff in the desktop panel (or a visible Chromium window
for CLI sessions). Automation pauses while you enter credentials or complete
MFA. Resume through the trusted host control to refresh page observations; a
model cannot resume itself. Stop
closes the owned context and releases temporary artifacts. Do not infer that a
submitted action was undone because Stop or a timeout occurred; inspect remote
state before repeating an uncertain operation.

## Observations and limitations

The core tool namespace covers owned session lifecycle, tabs, navigation,
semantic snapshots, current-target interaction, bounded diagnostics and private
screenshots. DOM observations normally avoid screenshots. A successful
navigation, HTTP 200 response or isolated click does not prove a development
task complete; use current source-bound verification and the existing quality
gates. The coding-only `browser_verify` tool checks explicit assertions and
records source-bound evidence, rather than accepting a model's assertion of
success as proof.

No browser MCP facade, persistent login profile, arbitrary model JavaScript, CDP,
model coordinate clicks, unrestricted file uploads, automatic downloads, video or
cross-browser engine is enabled. Panel input is a separate trusted host path;
raw coordinate/keyboard tools are not registered with the model. Browser access for child agents must remain
explicit and narrower than the parent's effective capabilities; child evidence
does not replace the parent's completion gate.

Network interception is application policy and does not confine Chromium at the
OS/network layer. Strict network isolation is currently rejected. Pages can
contain private data; redaction patterns cannot recognize every secret in
arbitrary text or pixels. Browser screenshots and authenticated content should
only be captured under the relevant host permission. Provider image encoding
tests are offline protocol tests; live model vision remains unverified.
Redirect destinations are validated before the next request; public HTTP and
private targets remain blocked. Redirects must never automatically replay an
uncertain form submission. Top-level page redirects preserve the actual destination
URL. Subresource redirects that change origin are blocked because fetching them
outside Chromium would bypass its CORS/origin handling. Applications that rely
on those redirects, WebSockets or service workers may still need capabilities
beyond this implementation.

For ownership, execution boundaries, recovery and provider details see
[BROWSER_ARCHITECTURE.md](BROWSER_ARCHITECTURE.md). The final execution report
records implemented and verified scope, remaining work and platform evidence.
