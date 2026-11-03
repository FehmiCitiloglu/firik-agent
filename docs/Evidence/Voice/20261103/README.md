# Unified voice request integration — 3 November 2026

This checkpoint documents the completed voice-to-application routing work in the
existing checkout. Voice is an input/output layer over the application's actual
chat and coding sessions. It does not introduce another conversation database,
agent loop, tool executor or recovery system.

## Request and session ownership

[`VoiceRequestRouter`](../../../../firik_agent/voice/intent_router.py) produces a
[`VoiceRequest`](../../../../firik_agent/voice/requests.py) from finalized ASR
text. The request retains its original transcript separately from the reviewed
request text, utterance ID, existing session/project IDs, target, UTC timestamp,
cancellation token and captured scope revision. Editing resets transcript review
without replacing the original utterance or application session.

Coding capture also binds the project's **canonical workspace root** as
`workspace_id`. The service checks that root against the existing project record;
the coding bridge checks the execution CWD against the same captured identity.
Changing the root under an unchanged project/session ID therefore requires a new
capture. Ordinary chat requests cannot acquire an unreviewed prompt, attachment
or CWD at the service boundary. Expired/replaced review objects, cancelled
requests, changed scopes/revisions and duplicate dispatches are rejected through
the existing router/service paths.

Recognized language is provider evidence, not the configured ASR language hint.
[`FinalTranscript`](../../../../firik_agent/voice/transcription.py) preserves that
metadata through trimming and review. A missing provider language remains absent;
segmented recognition reports one language only when every segment supplied the
same nonempty language. This metadata does not translate the transcript or
change the chat/coding model.

## Existing application paths and permissions

- Chat enters the normal `ChatView` composer and
  `FirikService.chat_agent_stream_async`, retaining the same `ChatSession`, model,
  history, context, skills, plugins and session-enabled MCP tools.
- Coding enters its normal composer, project context preparation, coding bridge,
  runtime control and tool-filter chain. The canonical workspace check narrows
  this path without adding an executor or a second session store.
- Navigation resolves actual view names, existing chat history/pages and project
  database entries. IDs match exactly and names must be unambiguous; navigation
  invokes the application's real existing actions.

Transcript confirmation is **not tool approval**, including for high impact
requests. Spoken approval phrases are rejected and ambiguous destructive requests
require clarification. Ordinary chat's
[`VoiceToolFilter`](../../../../firik_agent/voice/execution.py) adds exact-operation
approval to the existing `SessionToolFilter`: unknown/effectful operations need a
trusted asynchronous UI decision, and its small read-only exception list checks
tool source provenance. High impact requests do not use that exception. Disabled
tools and MCP server/tool policies stay authoritative; voice cannot enable them.

Chat and coding share the trusted
[`OperationApprovalDialog`](../../../../firik_agent/ui/components/operation_approval.py),
which displays read-only JSON for the exact proposed tool operation. Closing the
dialog rejects it. Cancellation, tool permissions and definition/handler identity
are rechecked before chat delegates execution; changed approval content is
rejected. Coding uses its existing authorization control and inherited policies,
with voice-origin writes narrowed to at least `ask` while preserving `deny`.
No partial ASR text dispatches a command, approval or tool call.

## Results and independent controls

Coding completion and read-last speech use the deterministic
[`coding_speech_summary`](../../../../firik_agent/voice/summaries.py): at most
600 characters, roughly three sentences, based on completed prose and supplied
project/outcome/activity/agent state. Recognized credential/URL/path patterns and
technical blocks are filtered for speech. Recorded errors/test-failure counts
take priority, and a completed run is not proof that tests passed. Full responses,
source, diffs, traces and tool evidence remain in the normal coding UI.

Stopping listening or TTS does not cancel an accepted agent request. Agent stop
uses the existing chat/coding cancellation action. Supported live coding
pause/resume controls act at their existing operation boundaries; they do not
invoke persisted-run recovery. Coding speaks its completed summary; ordinary
chat can use the existing optional stable-sentence speech queue.

## Verification ordering

The final 3 November backend and broad non-GUI reruns cover the latest
workspace/CWD/content refinements. The 2 November logs remain retained
checkpoints, not a claim that every earlier run covered those later changes.

| Checkpoint | Result and ordering |
| --- | --- |
| [Final broad non-GUI run, 3 November](non-gui-tests.log) | 1,974 passed, 3 skipped in 140.49 seconds. |
| [Final combined voice/coding/subagent backend run, 3 November](backend-tests.log) | 643 passed in 31.44 seconds, including the latest workspace/CWD/content checks. |
| Source grammar check | Passed for 80 Python source files. |
| [Broad non-GUI run, 2 November](../20261102/non-gui-tests.log) | 1,956 passed, 3 skipped; before the latest workspace/CWD/content checks. |
| [Combined voice/coding/subagent backend run, 2 November](../20261102/backend-tests.log) | 638 passed; before those final scope/content refinements. |
| [Full isolated native desktop run](../20261102/desktop-tests.log) | 78 cases, zero failures, including all 10 routing/UI cases and the workspace-root replacement case. Its artifact directory retains the 2 November checkpoint name. |
| [3 November native result index](desktop-results.json) | Records those same 78 unique cases and exit codes from the preceding full desktop log; it is an index, not an additional hardware run. |
| [Earlier routing-only native subset](../20261102/routing-desktop-tests.log) | 9 cases, zero failures; superseded in coverage by the full 78-case desktop run. |

After the final broad run started, the remaining changes aligned the PCM/WAV
file-picker labels and README wording with the validated API policy. These were
presentation-string/documentation changes; no additional functional backend
change followed that checkpoint. The final desktop result is 78 unique native
cases, not the sum of the full run and its earlier routing subset. Documentation
whitespace checks passed and all local links in this README and `docs/voice.md`
resolve.

The native runner launches a separate Tk process for each case. Tests exercise
real UI widgets and application state with injected audio/HTTP/agent dependencies;
production routes use live application inventories and the real service entry
points. Mock success is not evidence of microphone accuracy, acoustic behavior or
execution against an external service.

## Verification limits

No fresh real-microphone, audible-playback, Windows/Linux hardware or packaged
microphone-permission validation is established by this integration checkpoint.
The earlier native macOS synthesis check remains documented in the
[1 November evidence](../20261101/README.md).

The provider boundary retains the scope described in the
[2 November API discovery](../20261102/API_DISCOVERY.md) and
[provider implementation record](../20261102/IMPLEMENTATION.md). The public schema
was inspected, but no available credential authenticated model access or
inference in that checkpoint. This work does not establish new authenticated
provider capabilities, language quality, actual media types or server limits.
