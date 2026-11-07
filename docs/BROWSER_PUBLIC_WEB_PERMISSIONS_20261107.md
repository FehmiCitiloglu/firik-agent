# Public Web permission follow-up — 7 November 2026

The user's screenshot showed an approval for `mcp_web_web_search`, not an origin
grant. `SessionToolFilter` required exact approval for every unrelated tool after
browser content entered a conversation, including the builtin public Web reader.

Public Web search and reading now run without a per-call popup when the selected
tool belongs to Firik's genuine connected builtin Web implementation. The host
binds and rechecks its connection, client, server, tool definition and handler.
A name, description, handshake or copied schema cannot grant that exception.
Replacing/disconnecting the binding invalidates it, and the trusted handler never
auto-reconnects through changed configuration. Explicit MCP confirmation/denial
and chat tool/server selections remain effective. Unrelated tools retain their
existing approvals.

The Web reader uses the managed browser's shared public HTTPS URL policy. Every
GET and redirect validates the scheme, credentials, DNS results and address
classification before requesting the destination. Public HTTP and local/private
addresses are rejected; certificate verification stays enabled. Redirects are
bounded and automatic proxy/redirect following is disabled. These requests use
no personal browser profile or caller-selected headers/body. Page and search
limits remain in place. DNS checking is host application policy, with the same
separate-resolution limitation documented for the managed browser; it is not an
OS egress boundary or a site-reputation service.

Coding sessions with a host public HTTPS grant also default to `browser.navigate`
without a per-site prompt. Explicit policy, inherited limits and plan/question
modes continue to constrain that default. Forms and other state-changing browser
interactions retain their existing approval path. Agents are directed to open
observed links with `browser_navigate` for ordinary browsing.

Evidence is retained under
[public-web-permissions](Evidence/Browser/20261107/public-web-permissions/):
the live reproduction uses the real selected builtin Web MCP after browser taint,
searches the web, reads unrelated public HTTPS sites and attempts blocked HTTP
and loopback URLs without requesting any approval. No model/provider request,
login, personal profile, or external write is used.

- [Live acceptance](Evidence/Browser/20261107/public-web-permissions/live-public-web.json):
  zero approval requests; real DuckDuckGo search, Example Domain and redirected
  Playwright page reads succeeded. HTTP and loopback destinations were rejected.
- [Focused tests](Evidence/Browser/20261107/public-web-permissions/focused.log):
  164 passed, including spoof/replacement, explicit policy, restored browser
  history, network guards and unrelated MCP replay/redaction regressions.
- [Filtered broader regression](Evidence/Browser/20261107/public-web-permissions/regression.log):
  2,289 passed, 25 skipped, 457 deselected. Native UI/live/Docker cases were filtered.
- [Frozen macOS check](Evidence/Browser/20261107/public-web-permissions/frozen-smoke.log):
  UI/PortAudio and Chromium driver probes exited successfully. The disposable UI
  teardown also emitted an ignored asyncio invalid-file-descriptor finalizer
  warning; it did not fail the probes and is not claimed resolved by this change.
- [Installation](Evidence/Browser/20261107/public-web-permissions/installation.json):
  the verified local 0.2.48 macOS arm64 bundle replaced `/Applications/firik-agent.app`
  and was relaunched. The prior bundle is retained for rollback. No release was
  published. Native Windows/Linux and live provider-driven site research were not
  tested by this follow-up.

Only the provenance-verified builtin reader preserves bounded, sanitized Web
errors. HTTPS/private-address failures no longer claim an unknown mutation or
hide the reason behind a generic remote MCP error. Other MCPs retain their
existing error redaction and replay protection.
