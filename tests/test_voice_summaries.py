"""Coding speech is concise prose with actual state, never raw tool evidence."""
from dataclasses import dataclass

import pytest

from firik_agent.voice.summaries import MAX_SUMMARY_CHARS, agent_activity_summary, coding_speech_summary


def test_mixed_turkish_english_prose_preserves_actual_result_and_validation_limit():
    summary = coding_speech_summary(
        "Önbellek güncellendi. Tests passed. Gerçek cihaz doğrulaması eksik.",
        outcome="completed", project_name="Firik Agent")
    assert summary.startswith("Firik Agent:")
    assert "Önbellek güncellendi" in summary and "Tests passed" in summary
    assert "Gerçek cihaz doğrulaması eksik" in summary
    assert len(summary) <= MAX_SUMMARY_CHARS


def test_manual_validation_caveat_is_prioritized_over_more_implementation_prose():
    summary = coding_speech_summary("Updated the engine. Simplified the views. Changed the dispatcher. Manual device validation remains.")
    assert "Updated the engine" in summary and "Manual device validation remains" in summary
    assert "Changed the dispatcher" not in summary


@pytest.mark.parametrize("secret", [
    "sk-proj-abcdefghijklmnopqrstuv0123456789",
    "ghp_123456789012345678901234567890123456",
    "github_pat_abcdefghijklmnopqrstuv0123456789",
    "AKIA1234567890ABCDEF",
    "xoxb-12345678901234567890",
    'password="hunter2"',
    "API_KEY=plaintext-secret-value",
    "Authorization: Bearer bearer-value",
    "Authorization: Basic YWJjZGVmZw==",
    "Cookie: session=shortsecret",
    "the token is shortsecret",
    "parola eşittir kısa-sır",
])
def test_secret_patterns_are_not_spoken(secret):
    summary = coding_speech_summary("Configuration updated. " + secret)
    assert "Configuration updated" in summary
    for token in ("abcdefghijklmnopqrstuv", "1234567890ABCDEF", "hunter2", "plaintext-secret", "bearer-value",
                  "YWJjZGVmZw", "shortsecret", "kısa-sır"):
        assert token not in summary
    assert "SECRET_FILTERED" not in summary


@pytest.mark.parametrize("url", [
    "https://user:password@example.com?code=unlisted-secret",
    "https://example.com/callback?session=unlisted-secret&auth=other-secret",
    "postgresql://admin:db-secret@db.example.com/private",
    "mongodb+srv://user:db-secret@cluster.example.com/?code=unlisted-secret",
    "ssh://user:short-secret@hostname/private",
    "www.example.com/path?key=unlisted-secret",
])
def test_full_urls_and_query_credentials_are_removed_before_partial_secret_redaction(url):
    summary = coding_speech_summary(f"Connection to {url} was updated.")
    assert "unlisted-secret" not in summary and "short-secret" not in summary and "db-secret" not in summary
    assert "example.com" not in summary and "hostname" not in summary and "://" not in summary


@pytest.mark.parametrize("path", [
    "/Users/private-person/secret-project/password.txt",
    '"/Users/private-person/My Project/password.txt"',
    r"C:\Users\private-person\password.txt",
    r'"C:\Users\private-person\My Project\password.txt"',
    r"\\private-server\credentials\password.txt",
    "src/private-passwords.py",
    "../../private/credentials.json",
])
def test_paths_remain_in_normal_ui_instead_of_speech(path):
    summary = coding_speech_summary(f"The configuration at {path} was updated.")
    assert "private-person" not in summary and "password.txt" not in summary and "private-server" not in summary
    assert "credentials.json" not in summary and "private-passwords" not in summary


def test_nested_hidden_reasoning_and_code_are_not_repeated():
    text = "Visible result. <think>private outer <analysis>private inner</analysis> still private</think>\n```python\nprint('source secret')\n```\nTests were not run."
    summary = coding_speech_summary(text)
    assert "Visible result" in summary and "Tests were not run" in summary
    assert all(value not in summary for value in ("private", "source secret", "print", "think", "analysis"))


@pytest.mark.parametrize("text", [
    "```python\nprint('All tests passed')\n```",
    "def handler():\n    return 'All tests passed'",
    'print("All tests passed")',
    'const success = "All tests passed";\nreturn success;',
    '{"status": "All tests passed", "token": "secret"}',
    "diff --git a/app.py b/app.py\nindex abc123..def456\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+print('All tests passed')",
    "2026-11-02 12:10:00 INFO All tests passed\nDEBUG completed step 2",
])
def test_code_diff_json_and_log_only_input_do_not_invent_success(text):
    summary = coding_speech_summary(text)
    assert "All tests passed" not in summary and "success" not in summary
    assert summary == "Yanıt hazır; kod ve teknik ayrıntılar ekranda."


def test_traceback_source_that_mentions_test_success_never_claims_tests_passed():
    text = '''Traceback (most recent call last):
  File "/Users/private/project/test_engine.py", line 42, in check
    print("All tests passed")
    assert result == "tests passed"
AssertionError: API_KEY=actual-secret
'''
    summary = coding_speech_summary(text)
    assert "hata" in summary and "AssertionError" in summary
    assert "passed" not in summary.casefold() and "actual-secret" not in summary
    assert "Traceback" not in summary and "/Users" not in summary and "print" not in summary


def test_python_code_example_of_traceback_is_not_an_actual_run_failure():
    text = "The parser now recognizes failures.\n```python\nexample = 'Traceback (most recent call last):'\nraise ValueError('example')\n```"
    summary = coding_speech_summary(text)
    assert "The parser now recognizes failures" in summary
    assert "hata ile sona erdi" not in summary and "ValueError" not in summary


def test_complete_pytest_session_log_is_evidence_only_and_never_read_as_prose():
    text = '''============================= test session starts =============================
platform darwin -- Python 3.14, pytest-8.4, pluggy-1.6
rootdir: /Users/private/project
plugins: asyncio-1.0
collected 2 items
tests/test_engine.py .. [100%]
============================== 2 passed in 0.04s ==============================
'''
    assert coding_speech_summary(text) == "Yanıt hazır; kod ve teknik ayrıntılar ekranda."
    summary = coding_speech_summary(text + "\nUpdated the engine. Manual checks remain.")
    assert "Updated the engine" in summary and "Manual checks remain" in summary
    assert "pytest" not in summary and "2 passed" not in summary


def test_generic_node_error_is_failure_evidence_without_speaking_its_message_or_stack():
    text = "Error: Cannot find module '/Users/private/module.js'\n    at Module._compile (node:internal/loader:100:2)"
    summary = coding_speech_summary(text)
    assert summary == "Çalışma hata ile sona erdi: Error."


def test_failed_outcome_overrides_upbeat_assistant_claim_without_repeating_source():
    summary = coding_speech_summary("All changes are complete. Everything passed.", outcome="failed")
    assert summary == "Çalışma hata ile sona erdi."


def test_actual_failure_reason_is_selected_from_human_prose():
    summary = coding_speech_summary("I tried the build. Compilation failed because the SDK is missing.", outcome="failed")
    assert "hata ile sona erdi" in summary and "SDK is missing" in summary
    assert "I tried" not in summary


@pytest.mark.parametrize("status,phrase", [("cancelled", "durduruldu"), ("blocked", "doğrulanmadı"), ("outcome_unknown", "doğrulanmadı")])
def test_actual_non_success_outcomes_are_reported(status, phrase):
    summary = coding_speech_summary("Everything succeeded.", outcome=status)
    assert phrase in summary and "succeeded" not in summary


def test_completed_run_does_not_mean_tests_passed_when_only_code_was_returned():
    summary = coding_speech_summary("```python\nprint('All tests passed')\n```", outcome="completed")
    assert summary == "Çalışma sona erdi; ayrıntılar ekranda."


@pytest.mark.parametrize("output,count", [
    ("=== 2 failed, 10 passed in 1.5s ===", 2),
    ("1 failed, 2 errors, 5 passed in 3.0s", 3),
    ("FAILED (failures=1, errors=2)", 3),
    ("Tests: 2 failed, 8 passed, 10 total", 2),
])
def test_actual_test_failure_summary_reports_failed_count_without_reading_logs(output, count):
    summary = coding_speech_summary("Traceback (most recent call last):\n    print('999 passed')\nAssertionError: private\n" + output)
    assert f"{count} test başarısız" in summary
    assert "passed" not in summary and "private" not in summary


def test_actual_failed_tool_evidence_is_used_without_speaking_arguments_or_raw_output():
    activities = [{"tool": "run_tests", "status": "Hata", "arguments": {"token": "private-secret"},
                   "output": "Exit code 1:\nTraceback (most recent call last):\n  File '/private/path.py'\nValueError: password=secret\n2 failed, 8 passed in 1.0s"}]
    summary = coding_speech_summary("Updated the engine.", outcome="failed", activities=activities)
    assert "ValueError" in summary and "2 test başarısız" in summary
    assert "private-secret" not in summary and "path.py" not in summary and "Exit code" not in summary


def test_recovered_tool_error_does_not_falsely_turn_completed_run_into_failed_run():
    summary = coding_speech_summary("Updated the handler.", outcome="completed", activities=[{"tool": "read_file", "status": "Hata", "output": "FileNotFoundError: secret path"}])
    assert "1 araç işleminde hata" in summary and "Updated the handler" in summary
    assert "Çalışma hata ile sona erdi" not in summary and "secret path" not in summary


def test_structured_exception_reports_only_class_without_raw_message():
    summary = coding_speech_summary("", outcome=PermissionError("password=secret /Users/private/data"))
    assert summary == "Çalışma hata ile sona erdi: PermissionError."


def test_agent_summary_uses_real_orchestrator_and_subagent_counts():
    agents = {"orchestrator": {"role": "orchestrator", "status": "running", "task": "password=do-not-speak"},
              "worker-a": {"status": "testing"}, "worker-b": {"status": "completed"},
              "worker-c": {"status": "failed"}, "worker-d": {"status": "queued"}}
    summary = agent_activity_summary(agents)
    assert "Ana ajan çalışıyor" in summary
    assert "1 alt ajan çalışıyor" in summary and "1 alt ajan tamamlandı" in summary
    assert "1 alt ajan hata verdi" in summary and "1 alt ajan bekliyor" in summary
    assert "do-not-speak" not in summary and "worker-a" not in summary


def test_agent_summary_has_no_fabricated_root_or_children_when_absent():
    assert agent_activity_summary(None) == "Ajan durumu henüz bildirilmedi."
    summary = agent_activity_summary({"one": {"status": "running"}})
    assert "1 alt ajan çalışıyor" in summary and "Ana ajan" not in summary


def test_activity_agent_records_are_counted_by_identity_using_latest_record():
    activities = [{"agent_id": "worker", "status": "Çalışıyor", "output": "secret"},
                  {"agent_id": "worker", "status": "Tamamlandı", "output": "source code"},
                  {"tool": "run_command", "status": "Çalışıyor"}]
    summary = agent_activity_summary(None, activities)
    assert "1 alt ajan tamamlandı" in summary and "alt ajan çalışıyor" not in summary
    assert "1 araç işlemi sürüyor" in summary and "secret" not in summary


def test_actual_root_failure_is_used_when_explicit_outcome_is_absent():
    summary = coding_speech_summary("Everything passed.", agents={"orchestrator": {"status": "failed"}})
    assert "hata ile sona erdi" in summary and "Everything passed" not in summary


@dataclass
class Activity:
    status: str
    tool_name: str = "run_tests"
    detail: str = "private raw logs"


def test_activityentry_style_objects_are_supported_without_reading_raw_details():
    summary = agent_activity_summary(None, [Activity("running"), Activity("failed")])
    assert "1 araç işlemi sürüyor" in summary and "1 araç işleminde hata" in summary
    assert "private" not in summary


def test_summary_length_is_bounded_and_final_failure_survives_large_logs():
    text = "INFO source output\n" * 10000 + "\nTraceback (most recent call last):\nValueError: private\n"
    summary = coding_speech_summary(text, project_name="A" * 2000)
    assert len(summary) <= MAX_SUMMARY_CHARS and "ValueError" in summary
    assert "INFO" not in summary and "private" not in summary
    prose = coding_speech_summary("This is a very long implementation statement " * 1000)
    assert len(prose) <= MAX_SUMMARY_CHARS


def test_invalid_optional_values_do_not_block_text_or_fabricate_status():
    assert coding_speech_summary(None, activities=None, agents="invalid") == "Yanıt hazır; kod ve teknik ayrıntılar ekranda."
    assert agent_activity_summary("invalid", "invalid") == "Ajan durumu henüz bildirilmedi."


def test_project_name_uses_same_redaction_boundary_as_result():
    summary = coding_speech_summary("Updated the engine.", project_name="password=secret-value")
    assert summary == "Updated the engine."


def test_summary_is_deterministic_and_keeps_full_input_untouched():
    text = "Updated the engine. Tests were not run."
    activities = [{"tool": "run_command", "status": "Tamamlandı", "output": "full UI details"}]
    before = repr(activities)
    assert coding_speech_summary(text, activities=activities) == coding_speech_summary(text, activities=activities)
    assert repr(activities) == before and text == "Updated the engine. Tests were not run."
