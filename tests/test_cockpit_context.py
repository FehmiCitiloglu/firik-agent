"""Real context preparation, provider evidence and composer scope regressions."""
from __future__ import annotations

import json

import httpx
import pytest

from firik_agent.core.types import Message, ModelInfo
from firik_agent.projects.coding_service import ProjectCodingService
from firik_agent.projects.composer import composer_suggestions, parse_composer
from firik_agent.projects.context_inspector import inspect_request_context
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.indexer import CodebaseIndexer
from firik_agent.projects.model_capabilities import estimate_usage_cost, model_capabilities
from firik_agent.projects.models import Project, ProjectMemory
from firik_agent.providers.openai_provider import OpenAIProvider


def project_service(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("from private import helper\nclass FeatureService: pass\n", encoding="utf-8")
    (root / "private.py").write_text("PRIVATE_SOURCE_MARKER = 'FeatureService'\n", encoding="utf-8")
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Proje", local_path=str(root)))
    for path in (root / "src" / "main.py", root / "private.py"):
        db.save_code_index("p", **CodebaseIndexer(root).index_file(path))
    return ProjectCodingService(db), db, root


def test_never_include_wins_over_pins_mentions_search_dependencies_and_diff(tmp_path, monkeypatch):
    coding, db, root = project_service(tmp_path)
    settings = db.get_settings("p")
    settings.coding_preferences.update(pinned_files=["private.py", "src"], always_include_files=["private.py"], excluded_files=["private.py"])
    db.save_settings(settings)
    diff = "diff --git a/private.py b/private.py\n--- a/private.py\n+++ b/private.py\n+PRIVATE_DIFF_MARKER\ndiff --git a/src/main.py b/src/main.py\n--- a/src/main.py\n+++ b/src/main.py\n+SAFE_DIFF_MARKER\n"
    monkeypatch.setattr("firik_agent.projects.coding_service.GitService.get_diff", lambda *args, **kwargs: diff)
    from firik_agent.projects.indexer import read_project_text
    reads = []
    def read(project, path, *args, **kwargs):
        reads.append(str(path))
        assert str(path) != "private.py"
        return read_project_text(project, path, *args, **kwargs)
    monkeypatch.setattr("firik_agent.projects.coding_service.read_project_text", read)
    monkeypatch.setattr("firik_agent.projects.indexer.read_project_text", read)
    result = coding.prepare_coding_prompt("p", "FeatureService @file:private.py")
    assert result["relevant_files"] == ["src/main.py"]
    assert "PRIVATE_SOURCE_MARKER" not in result["system_prompt"]
    assert "PRIVATE_DIFF_MARKER" not in result["system_prompt"]
    assert "SAFE_DIFF_MARKER" in result["system_prompt"]
    assert reads and "private.py" in result["unavailable_files"]
    file = next(item for item in result["context_items"] if item.get("path"))
    assert file["pinned"] and file["source"] == "pinned" and file["included"]
    assert result["index_status"]["files"] == 2 and result["index_status"]["semantic_index_available"] is False


def test_summary_is_a_bounded_source_excerpt_and_reports_actual_lines(tmp_path):
    coding, db, root = project_service(tmp_path)
    (root / "src" / "main.py").write_text("\n".join(f"line_{n} = {n}" for n in range(150)), encoding="utf-8")
    result = coding.prepare_coding_prompt("p", "dosyayı açıkla", always_include_paths=["src/main.py"], summarized_paths=["src/main.py"], excluded_paths=["private.py"])
    file = next(item for item in result["context_items"] if item.get("path"))
    assert file["summarized"] and file["always_include"] and file["line_end"] == 30
    assert "model tarafından üretilmiş özet değildir" in result["system_prompt"]
    assert "line_29" in result["system_prompt"] and "line_30" not in result["system_prompt"]


def test_request_inspector_counts_current_conversation_and_filtered_mcp_schemas():
    messages = [Message(role="system", content="kurallar" * 200), Message(role="user", content="soru" * 500), Message(role="tool", content="sonuç" * 100)]
    schemas = [{"type": "function", "function": {"name": "read_file", "description": "Read source"}},
               {"type": "function", "function": {"name": "mcp_git_get_issue", "description": "Get real issue"}}]
    report = inspect_request_context(messages, schemas, {"context_window": 4000}, visibility_report=[{"key": "instructions", "tokens_used": 40}])
    counts = {item["key"]: item["tokens"] for item in report["categories"]}
    assert counts["conversation"] > 500 and counts["mcp_context"] > 0 and counts["tool_schemas"] > 0
    assert counts["project_rules"] == 40
    assert report["estimated_input_tokens"] == sum(counts.values())
    assert report["fill_ratio"] == report["estimated_input_tokens"] / 4000
    assert report["estimate_only"] and "content" not in json.dumps(report)
    assert inspect_request_context(messages, schemas)["fill_ratio"] is None


def test_cache_cost_uses_only_verified_prices_and_reported_token_counts():
    usage = {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_tokens_details": {"cached_tokens": 400}}
    assert estimate_usage_cost(usage, {"input_cost_per_million": 2, "output_cost_per_million": 8})["cost"] is None
    capabilities = model_capabilities({"source": "provider", "input_cost_per_million": 2, "output_cost_per_million": 8, "cached_input_cost_per_million": 0.5})
    priced = estimate_usage_cost(usage, capabilities)
    assert priced["cost"] == pytest.approx(0.003)
    assert priced["cached_tokens"] == 400 and priced["total_tokens"] == 1200 and priced["estimate_only"]
    assert estimate_usage_cost({}, capabilities)["cost"] is None
    reported = estimate_usage_cost({"cost": 0}, {})
    assert reported["cost"] == 0 and reported["source"] == "provider" and not reported["estimate_only"]


def test_composer_commands_references_and_suggestions_are_explicit():
    parsed = parse_composer('/plan İncele @file:"src/my module.py" @memory:approved @agent:worker @terminal:run1 @commit:1234567 @skill:review')
    assert parsed["command"] == "plan" and parsed["mode"] == "plan"
    assert parsed["references"][0] == {"kind": "file", "value": "src/my module.py"}
    assert len(parsed["references"]) == 6
    choices = composer_suggestions("@", files=["src/my module.py", ".env", "../outside.py"], memories=[{"id": "m", "content": "Onaylı hafıza"}])
    assert [choice["id"] for choice in choices] == ["src/my module.py", "m"]
    assert choices[0]["insert"] == '@file:"src/my module.py" '
    assert composer_suggestions("/re")[0]["insert"] == "/review "
    assert parse_composer("/mcp tool")["mode"] is None


def test_memory_agent_terminal_references_are_project_scoped_and_sanitized(tmp_path):
    coding, db, root = project_service(tmp_path)
    db.create_project(Project(id="other", name="Başka", local_path=str(root)))
    db.save_memory(ProjectMemory(id="other-memory", project_id="other", content="OTHER_PROJECT_MARKER"))
    db.save_memory(ProjectMemory(id="approved", project_id="p", content='api_key="memory-secret"; tercih: Türkçe'))
    result = coding.prepare_coding_prompt("p", "@memory:approved @memory:other-memory @agent:other-agent @terminal:live",
        composer_context={"agents": [{"id": "other-agent", "project_id": "other", "content": "OTHER_AGENT_MARKER"}],
                          "terminals": [{"id": "live", "project_id": "p", "output": 'token="terminal-secret"'}]})
    prompt = result["system_prompt"]
    assert "OTHER_PROJECT_MARKER" not in prompt and "OTHER_AGENT_MARKER" not in prompt
    assert "memory-secret" not in prompt and "terminal-secret" not in prompt
    assert "memory-secret" not in json.dumps(result["context_items"])
    report = {item["value"]: item["resolved"] for item in result["composer"]["reference_report"]}
    assert report == {"approved": True, "other-memory": False, "other-agent": False, "live": True}


@pytest.mark.asyncio
async def test_openai_compatible_discovery_keeps_absent_capabilities_unknown(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "local-model"}, {"id": "verified", "context_window": 8192, "supports_tools": False}]})), **kwargs))
    provider = OpenAIProvider(name="ollama", base_url="http://127.0.0.1:11434/v1", default_model="local-model")
    models = await provider.list_models()
    assert models[0].context_window is None and models[0].supports_tools is None and models[0].local
    assert models[1].context_window == 8192 and models[1].supports_tools is False
    assert ModelInfo(id="custom", name="Custom").context_window is None
    health = await provider.endpoint_health()
    assert health["status"] == "online" and health["available_models"] == 2


@pytest.mark.asyncio
async def test_compatible_endpoint_failure_is_not_fake_openai_models_or_online(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, text="Unavailable")), **kwargs))
    provider = OpenAIProvider(name="local", base_url="http://localhost:1234/v1", default_model="chosen-local")
    assert [model.id for model in await provider.list_models()] == ["chosen-local"]
    assert (await provider.endpoint_health())["status"] == "error"


@pytest.mark.asyncio
async def test_openai_stream_reports_usage_tail_and_reasoning(monkeypatch):
    usage = {"prompt_tokens": 30, "completion_tokens": 4, "total_tokens": 34, "prompt_tokens_details": {"cached_tokens": 12}}
    frames = [{"choices": [{"delta": {"reasoning_content": "düşün", "content": "yanıt"}}]}, {"choices": [], "usage": usage}]
    payload = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames) + "data: [DONE]\n\n"
    original = httpx.AsyncClient
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, text=payload)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    chunks = [chunk async for chunk in OpenAIProvider().chat_stream([Message(role="user", content="merhaba")])]
    assert any(chunk.delta_reasoning == "düşün" for chunk in chunks)
    assert [chunk.usage for chunk in chunks if chunk.usage] == [usage]
    assert requests[0]["stream_options"]["include_usage"] is True
