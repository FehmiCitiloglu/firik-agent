# Harness selection and desktop experiments

The native Firik harness remains the default. Existing configuration, model
selection, project histories, MCP and permission settings continue to work.
The harness owns orchestration; the model provider supplies generation; the
execution environment owns the actual filesystem/process boundary.

## Configuration

Add an optional section to the existing user YAML or project coding preferences:

```yaml
harness:
  default_backend: native
  profile: coding
  backends:
    native:
      enabled: true
    pi:
      enabled: false
    codex:
      enabled: false
  optimization:
    benchmark_based_suggestions: true
    automatic_backend_switching: false
```

Backend settings accept only `enabled`, optional `executable`, and optional
`expected_version`. Unsupported fields and disabled selections fail explicitly.
Enabling a backend is configuration intent, not evidence that its software,
version or security boundary is available. Discovery reports those separately.
No installation, credential lookup or model request happens during discovery.

User configuration is followed by project preferences. The existing project
`harness_profile` selection takes precedence over the new profile field. No
backend changes while a task is active, and no automatic switching is currently
implemented. A profile never grants permissions.

```sh
firik-agent harness settings
firik-agent harness backends
firik-agent harness backends --config /path/to/config.yaml
```

Both `firik harness` and `python -m firik_agent.benchmarks` expose the same
commands. Existing `doctor`, `profiles`, `context`, `bench` and `compare` routes
remain available. `settings` prints the harness section only, excluding provider
credentials even when an explicit configuration file contains them.

## Desktop

Open a project's coding workspace, then **Harness / Benchmark** in the task
inspector. The dialog has backend availability/version/security details,
validated comparison reports, the current execution trace and experiments.
Opening it dispatches no model requests. Discovery is performed off the GUI
thread and results go through the existing application callback queue.

Native is the selectable project coding harness. External tool-free generation
does not implement Firik's filesystem authorization, approvals or quality gates;
external project coding is disabled until a verified mapping exists. The view
still reports external installations and their actual capabilities. The existing
model and profile selectors remain the configuration controls for native coding.

The execution trace projects existing task state, steps, tool/error/recovery
activity, usage and verification evidence. It retains only the latest 200
activity records in this view; the existing persistent journal remains the
source of history. Backend outputs and loaded reports are untrusted display
data. Credential-like values are redacted before display/export.

**Rapor Aç** validates a backend comparison report and recomputes summaries
before showing measured success, duration, token/cost coverage, tool/error and
recovery counts, model revision evidence, and recommendations. Unknown costs and
usage remain unknown. A recommendation does not change a project or active task.

## Explicit experiments

An experiment uses the versioned `ComparisonPlan` schema described in
[HARNESS_COMPARISON.md](HARNESS_COMPARISON.md). Each configuration receives an
independent temporary Git repository with the same pinned task sources and
host-controlled evaluator. No candidate changes are integrated into the user's
project. The default factory is a credential-free native synthetic replay;
real providers need an explicitly authorized, host-bound factory. A backend
that cannot enforce the requested policy produces a failed row with the reason.

```sh
firik-agent harness experiment --plan /path/to/plan.json --output /path/to/new-report.json
firik-agent harness recommend --report /path/to/report.json --objective balanced
```

Every non-replay provider requires `--allow-paid`, including plans that describe
the model as local. That flag authorizes the named experiment; it neither finds
credentials nor constructs an unrestricted runtime. Live host integrations must
bind the provider and its permissions explicitly. Output files are created
exclusively and existing reports are never overwritten.

In the desktop choose **Deney Planı Çalıştır** and review the displayed backend,
provider and model list before the per-plan confirmation. All configurations
are covered by that confirmation; non-replay providers may incur charges.
**Deneyi Durdur** propagates cancellation, and closing the dialog cancels active
background work so the comparison runner can tear down its sessions. A second
experiment cannot start while the current future is active. Opening a report,
refreshing availability, or requesting a recommendation never starts a run.

## Explicit live tool-free Pi prompts

The Pi adapter has a separate useful entry point for generation without coding
tools. Enable the audited Pi version in an explicit configuration file, then
use a provider key stored in an environment variable that you name for this run:

```sh
firik-agent harness prompt --backend pi --provider openai --model MODEL_ID \
  --prompt "Explain a pure function" --credential-env MY_EXPLICIT_PROVIDER_KEY \
  --allow-live --config /path/to/pi-enabled.yaml
```

This command may incur provider charges. It was not run against a live provider
during implementation. `openai`, `anthropic`, and `google` are the admitted
provider names. `--allow-live` authorizes this prompt and provider network access;
it is required before the named credential variable is read. Backend enablement,
installation/version, bounded input, and timeout are checked first. No API key
from an existing config, keychain, Pi login, dotenv file, or other environment
variable is imported. Never put a key value in a command argument.

Only the explicitly supplied prompt and provider key are sent to the selected
provider. The session has no workspace, tools, MCP, extensions, skills, project
context or resume state. A private ephemeral configuration/home directory is
not an OS sandbox. The result reports actual model identity, backend errors,
text and available usage; unknown usage/cost remain null, and completion has no
verified coding evidence. Session cancellation/close always run, and the named
key is removed from output/errors using exact redaction in addition to the
existing credential filters. `--timeout` sets a finite deadline; `--output` can
write a new normalized JSON result. Codex and external coding remain unavailable
under the documented policy restrictions.

## Validation scope

`tests/test_harness_config_cli.py` exercises passive configuration, early CLI
dispatch, strict policy validation, active-task protection, report size bounds,
trace bounds and redaction. `tests/test_ui_harness.py` verifies the real Tk
dialog, asynchronous passive discovery and project preference persistence in a
GUI-capable environment. Comparative and adapter lifecycle tests live in their
own suites; successful desktop rendering does not establish live provider or
container isolation coverage.
