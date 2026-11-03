"""Deterministic coding speech summaries; full evidence remains in the UI.

Only prose and recorded state are summarized. This module never executes a
command, calls a model, interprets tool arguments, or treats a returned tool
result as proof that tests passed.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import islice
import re

from firik_agent.projects.indexer import sanitize_secrets
from firik_agent.voice.speech import speech_text

MAX_SUMMARY_CHARS = 600
MAX_SOURCE_CHARS = 48_000
MAX_RECORDS = 100

_ACTIVE = {"running", "planning", "reviewing", "testing", "thinking", "executing",
           "implementing", "applying", "verifying", "correcting", "repairing", "çalışıyor"}
_WAITING = {"waiting", "queued", "created", "paused", "needs_approval", "waiting_approval", "awaiting_approval"}
_FAILED = {"failed", "error", "hata"}
_CANCELLED = {"cancelled", "canceled", "interrupted", "stopped", "kesildi", "durduruldu"}
_COMPLETED = {"completed", "completed_verified", "completed_unverified", "tamamlandı"}
_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,15}://[^\s<>]+|\bwww\.[^\s<>]+")
_PATH = re.compile(r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\|~?/|\.{1,2}/|[\w.-]+/)[^\s<>\"'|,;]*")
_EXCEPTION = re.compile(r"^\s*(?:E\s+)?(?:[\w]+\.)*([A-Za-z_]\w*(?:Error|Exception|Failure|Timeout|Interrupt)|Error|Exception|Failure|Timeout)\s*(?::|$)")
_TRACE = re.compile(r"^\s*(?:Traceback\s*\(|During handling of the above exception|The above exception was the direct cause)")
_LOG = re.compile(r"^\s*(?:\d{4}[-/]\d{2}[-/]\d{2}[T\s]|\[?\d{2}:\d{2}:\d{2}|\[?(?:DEBUG|INFO|WARN(?:ING)?|ERROR|CRITICAL|TRACE)\b)", re.I)
_SOURCE = re.compile(r"^\s*(?:diff --git\b|index [0-9a-f]+\.\.|@@|--- |\+\+\+ |[+-](?!\s*\w+\s)|[+\-]\s*(?:def |class |const |let |var |return |import |from )|(?:async\s+)?def\s|class\s+\w+\s*[:({]|(?:const|let|var|import|from|return|raise|assert|export)\s|[\w.]+\s*(?:=|\+=)|[{}\[\]]\s*$|[\"'][\w-]+[\"']\s*:)" )
_RAW = re.compile(r"^\s*(?:File\s+[\"']|at\s+\S+\s*\(|E(?:\s|$)|>\s*assert\b|collected \d+|=+\s*(?:FAILURES|ERRORS|short test)|[-=]{4,}|[.FsEx]+\s*(?:\[\s*\d+%\])?$|Ran \d+ tests?\b|(?:FAILED|PASSED|SKIPPED)\b|(?:Tests|Test Suites):)", re.I)
_PYTEST = re.compile(r"^(?:={2,}\s*)?((?:\d+\s+(?:failed|passed|skipped|errors?|warnings?|xfailed|xpassed|deselected)(?:,\s*|\s+|$))+)(?:in\s+[\d.]+s)?(?:\s*=+)?$", re.I)


def _value(record, name, default=None):
    return record.get(name, default) if isinstance(record, Mapping) else getattr(record, name, default)


def _status(value):
    return str(getattr(value, "value", value) or "").casefold().strip()


def _records(values):
    if values is None or isinstance(values, (str, bytes)):
        return []
    if isinstance(values, Mapping):
        if "status" in values or "tool" in values or "kind" in values:
            return [values]
        values = values.values()
    elif isinstance(values, Sequence):
        values = values[-MAX_RECORDS:]
    try:
        return [value for value in islice(values, MAX_RECORDS)
                if isinstance(value, Mapping) or hasattr(value, "status")]
    except TypeError:
        return []


def _bounded_source(value):
    if not isinstance(value, str):
        return ""
    if len(value) <= MAX_SOURCE_CHARS:
        return value
    # Keep complete lines from both ends, preserving final failure summaries
    # without beginning a retained tail in the middle of a credential.
    head = value[:MAX_SOURCE_CHARS // 2].rsplit("\n", 1)[0]
    tail = value[-MAX_SOURCE_CHARS // 2:]
    tail = tail.split("\n", 1)[1] if "\n" in tail else ""
    return head + "\n" + tail


def _clean(value, limit=400):
    value = _bounded_source(value)
    # Remove complete URLs before the shared sanitizer can replace just their
    # userinfo prefix and leave an otherwise innocuous-looking query behind.
    value = _URL.sub(" ", value)
    value = re.sub(r"(?i)\b(?:authorization|set-cookie|cookie|x-api-key)\s*[:=][^\r\n]+", " ", value)
    value = re.sub(r"(?i)\b(?:basic|bearer)\s+\S+", " ", value)
    value = sanitize_secrets(value)
    value = re.sub(r"(?i)\b(?:password|passwd|secret|token|api[ _-]?key|parola|şifre)\s+(?:is|was|equals|eşittir)\s+\S+", " ", value)
    value = re.sub(r"[\"'](?:[A-Za-z]:[\\/]|/|~[/\\]|\\\\)[^\"'\r\n]*[\"']", " ", value)
    value = _PATH.sub(" ", value)
    value = re.sub(r"\b[A-Za-z0-9_=-]{24,}(?:\.[A-Za-z0-9_=-]+)*\b", " ", value)
    value = value.replace("[SECRET_FILTERED]", " ")
    value = speech_text(value)
    # Unclosed inline Markdown must not turn source fragments into prose.
    value = re.sub(r"[`<].*$", "", value).strip(" \t\r\n:;,-")
    if len(value) > limit:
        value = value[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return value


def _hide_reasoning(text):
    pieces, stack, cursor = [], [], 0
    for match in re.finditer(r"</?(think|analysis|reasoning)\b[^>]*>", text, re.I):
        if not stack:
            pieces.append(text[cursor:match.start()])
        name = match.group(1).casefold()
        if match.group().startswith("</"):
            if stack and stack[-1] == name:
                stack.pop()
        else:
            stack.append(name)
        cursor = match.end()
        if not stack:
            pieces.append(" ")
    if not stack:
        pieces.append(text[cursor:])
    return "".join(pieces)


def _source_lines(text):
    """Return prose and diagnostics while excluding source, diff and logs."""
    text = _bounded_source(text)
    text = _hide_reasoning(text)
    text = _URL.sub(" ", text)
    text = re.sub(r"(?im)\b(?:authorization|set-cookie|cookie|x-api-key)\s*[:=][^\r\n]+", " ", text)
    text = re.sub(r"(?i)\b(?:basic|bearer)\s+\S+", " ", text)
    text = sanitize_secrets(text)
    prose, diagnostics = [], []
    fence = None
    fence_diagnostic = False
    diff = False
    test_log = False
    for line in text.splitlines():
        stripped = line.strip()
        marker = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            token, suffix = marker.groups()
            if fence is None:
                fence = token
                fence_diagnostic = suffix.strip().casefold() in {"", "text", "log", "console", "traceback"}
            elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip():
                fence = None
            continue
        if fence is not None:
            if fence_diagnostic:
                diagnostics.append(stripped)
            continue
        diagnostics.append(stripped)
        if re.match(r"^={2,}.*(?:test session starts|FAILURES|ERRORS|short test summary)", stripped, re.I):
            test_log = True
            continue
        if test_log:
            if _PYTEST.fullmatch(stripped):
                test_log = False
            continue
        if not stripped:
            diff = False
            continue
        if stripped.startswith(("diff --git ", "@@", "--- ", "+++ ")):
            diff = True
            continue
        if diff and line.startswith((" ", "+", "-", "\\")):
            continue
        diff = False
        if (line.startswith(("    ", "\t")) or _TRACE.match(line) or _EXCEPTION.match(line)
                or _SOURCE.match(line) or _RAW.match(line) or _LOG.match(line) or _PYTEST.fullmatch(stripped)):
            continue
        # Dense JSON, source calls and patch/source lines should stay on screen.
        if (stripped.startswith(("+", "-")) and not re.match(r"^[-+]\s+[A-ZÇĞİÖŞÜ]", stripped)
                or re.fullmatch(r"[\w.]+\([^\n]*\);?", stripped)
                or stripped.count("{") + stripped.count("}") > 1):
            continue
        value = _clean(line, limit=4000)
        if value and re.search(r"[^\W\d_]", value, re.UNICODE):
            prose.append(value)
    return prose, diagnostics


def _test_failures(lines):
    counts = []
    for line in lines:
        line = line.strip()
        if _PYTEST.fullmatch(line) or re.fullmatch(r"(?:Tests|Test Suites):\s*\d+.*", line, re.I):
            failed = sum(int(count) for count in re.findall(r"\b(\d+)\s+(?:failed|errors?)\b", line, re.I))
            if failed:
                counts.append(failed)
        else:
            match = re.fullmatch(r"FAILED\s*\(([^)]*)\)", line)
            if match:
                counts.append(sum(int(count) for count in re.findall(r"(?:failures|errors)\s*=\s*(\d+)", match.group(1))))
    return counts[-1] if counts else 0


def _activity_failed(record):
    exit_code = _value(record, "exit_code")
    return (_status(_value(record, "status")) in _FAILED or _value(record, "is_error") is True
            or bool(_value(record, "error"))
            or isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0)


def _agent_rows(agents, activities):
    if isinstance(agents, Mapping) and not ("id" in agents or "status" in agents):
        rows = [(str(key), value) for key, value in islice(agents.items(), MAX_RECORDS) if isinstance(value, Mapping)]
    else:
        rows = [(str(_value(row, "id", _value(row, "agent_id", ""))), row) for row in _records(agents)]
    if rows:
        return rows
    inferred = {}
    for row in _records(activities):
        identifier = _value(row, "agent_id")
        if isinstance(identifier, str) and identifier:
            inferred[identifier] = row
    return list(inferred.items())


def agent_activity_summary(agents, activities=()) -> str:
    """Describe only agent/tool statuses present in the supplied snapshot."""
    activities = _records(activities)
    rows = _agent_rows(agents, activities)
    clauses = []
    roots = [row for identifier, row in rows if identifier == "orchestrator" or _value(row, "role") == "orchestrator"]
    children = [row for identifier, row in rows if identifier != "orchestrator" and _value(row, "role") != "orchestrator"]
    if roots:
        status = _status(_value(roots[0], "status"))
        if status in _ACTIVE:
            clauses.append("Ana ajan çalışıyor")
        elif status in _WAITING:
            clauses.append("Ana ajan onay bekliyor" if "approval" in status else "Ana ajan bekliyor")
        elif status in _FAILED:
            clauses.append("Ana ajan hata ile durdu")
        elif status in _CANCELLED:
            clauses.append("Ana ajan durduruldu")
        elif status == "blocked":
            clauses.append("Ana ajan engellendi")
        elif status in _COMPLETED:
            clauses.append("Ana ajan çalışmasını tamamladı")
    groups = [(_ACTIVE, "alt ajan çalışıyor"), (_WAITING, "alt ajan bekliyor"),
              (_FAILED, "alt ajan hata verdi"), (_CANCELLED, "alt ajan durduruldu"),
              ({"blocked", "outcome_unknown"}, "alt ajanın sonucu henüz doğrulanmadı"),
              (_COMPLETED, "alt ajan tamamlandı")]
    for statuses, label in groups:
        count = sum(_status(_value(row, "status")) in statuses for row in children)
        if count:
            clauses.append(f"{count} {label}")
    active_tools = sum(_status(_value(row, "status")) in _ACTIVE
                       and bool(_value(row, "tool", _value(row, "tool_name")))
                       and not _value(row, "agent_id") for row in activities)
    if active_tools:
        clauses.append(f"{active_tools} araç işlemi sürüyor")
    failed_tools = sum(_activity_failed(row) and not _value(row, "agent_id") for row in activities)
    if failed_tools:
        clauses.append(f"{failed_tools} araç işleminde hata kaydedildi")
    return _finish(["; ".join(clauses)]) if clauses else "Ajan durumu henüz bildirilmedi."


def _finish(parts):
    text = " ".join(part.rstrip(" .!?…") + ("…" if part.endswith("…") else ".") for part in parts if part)
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    return text[:MAX_SUMMARY_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:.!?") + "…"


def coding_speech_summary(text, *, outcome=None, activities=(), agents=None, project_name=None) -> str:
    """Produce at most three concise sentences, prioritizing actual failures.

    ``completed`` means the recorded run ended; it never implies validation
    passed. Logs are evidence for failure counts/classes only, never speech.
    """
    activities = _records(activities)
    prose, diagnostic = _source_lines(text)
    actual_status = _status(_value(outcome, "status", _value(outcome, "outcome", ""))) if isinstance(outcome, Mapping) else _status(outcome)
    if isinstance(outcome, BaseException):
        actual_status = "failed"
        diagnostic.append(type(outcome).__name__ + ":")
    elif isinstance(outcome, Mapping):
        error = outcome.get("error")
        if isinstance(error, BaseException):
            diagnostic.append(type(error).__name__ + ":")
        elif isinstance(error, str):
            diagnostic.extend(_bounded_source(error).splitlines())
    rows = _agent_rows(agents, activities)
    if not actual_status:
        root = next((row for identifier, row in rows if identifier == "orchestrator" or _value(row, "role") == "orchestrator"), None)
        if root is not None:
            actual_status = _status(_value(root, "status"))
    for row in activities:
        if _activity_failed(row) or str(_value(row, "tool", _value(row, "tool_name", ""))) in {"run_tests", "run_quality_gate"}:
            for field in ("error", "output", "result", "detail"):
                value = _value(row, field)
                if isinstance(value, str):
                    diagnostic.extend(_bounded_source(value).splitlines())
    failed_tests = _test_failures(diagnostic)
    failed_tools = sum(_activity_failed(row) and not _value(row, "agent_id") for row in activities)
    exception = next((match.group(1) for line in reversed(diagnostic) if (match := _EXCEPTION.match(line))), None)
    exception = _clean(exception, limit=80) if exception else None
    raw_trace = any(_TRACE.match(line) for line in diagnostic)
    failure = bool(actual_status in _FAILED or not actual_status and (raw_trace or failed_tests or exception))
    parts = []
    if actual_status in _CANCELLED:
        parts.append("Çalışma durduruldu")
    elif actual_status in {"blocked", "outcome_unknown"}:
        parts.append("Çalışmanın tamamlandığı doğrulanmadı; bekleyen işlemler var")
    elif failure:
        parts.append("Çalışma hata ile sona erdi" + (f": {exception}" if exception else ""))
    if failed_tests:
        parts.append(f"Doğrulamada {failed_tests} test başarısız oldu")
    elif failed_tools:
        parts.append(f"{failed_tools} araç işleminde hata kaydedildi")
    sentences = []
    for line in prose:
        sentences.extend(part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9])", line) if part.strip())
    if failure and len(parts) < 3:
        reason = next((sentence for sentence in sentences if re.search(
            r"\b(?:failed|failure|hata|başarısız|could not|cannot|unable|missing|eksik|not found|bulunamadı)\b", sentence, re.I)
            and not re.search(r"\b(?:fixed|resolved|düzeltildi|giderildi)\b", sentence, re.I)), None)
        if reason:
            parts.append(_clean(reason, limit=200))
    if not failure and actual_status not in _CANCELLED | {"blocked", "outcome_unknown"}:
        # Human-written failure/verification/caveat prose is more useful than
        # a generic introduction; otherwise preserve the author's order.
        caveats = [sentence for sentence in sentences if re.search(r"\b(?:fail(?:ed|ure)?|error|blocked|unverified|not tested|not run|not verified|remain(?:s|ing)?|manual|pending|hata|başarısız|doğrulanmadı|eksik|bekliyor|kaldı|çalıştırılmadı)\b", sentence, re.I)]
        verification = [sentence for sentence in sentences if re.search(r"\b(?:tests?|doğrulama)\b", sentence, re.I)]
        selected = ([sentences[0]] if sentences else []) + caveats + verification + sentences[1:]
        for sentence in selected:
            clean = _clean(sentence, limit=200)
            if clean not in parts and len(parts) < (2 if rows else 3):
                parts.append(clean)
    if not parts:
        parts.append("Çalışma sona erdi; ayrıntılar ekranda" if actual_status in _COMPLETED
                     else "Yanıt hazır; kod ve teknik ayrıntılar ekranda")
    if rows and len(parts) < 3:
        status = agent_activity_summary(agents, activities)
        if status != "Ajan durumu henüz bildirilmedi.":
            parts.append(status)
    # Never consume a third slot with an extra generic prose sentence.
    result = _finish(parts[:3])
    project = _clean(project_name, limit=80) if isinstance(project_name, str) else ""
    if project:
        result = _finish([project + ": " + result])
    return result
