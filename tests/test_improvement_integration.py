"""Exercise learning at real persistence/context/runtime integration boundaries."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.projects.coding_service import ProjectCodingService
from firik_agent.projects.coding_sessions import CodingSessionStore
from firik_agent.projects.context_builder import ProjectContextBuilder, approx_tokens
from firik_agent.projects.db import ProjectDatabase
from firik_agent.projects.models import Project, ProjectInstruction


def database(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    db = ProjectDatabase(tmp_path / "projects.db")
    db.create_project(Project(id="p", name="Project", local_path=str(root)))
    db.create_project(Project(id="other", name="Other", local_path=str(root)))
    return db, root


def learned(identifier="lesson", project_id="p", **fields):
    return {"id": identifier, "scope": "project", "project_id": project_id,
            "content": "Run the packaging check after building the wheel.",
            "kind": "semantic", "status": "active", "user_approved": True,
            "verification_status": "verified", "confidence": 1.0,
            "reason": "Approved packaging workflow matches this task", **fields}


def test_durable_event_is_collected_after_commit_with_original_identity(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    store = CodingSessionStore(db)
    run = store.create_run("p", session_id="s", provider="configured", model="selected")
    observations = []

    def observe(event, **identity):
        # This would deadlock if learning ran inside the journal transaction.
        with db._lock:
            conn = db._get_connection()
            try:
                assert conn.execute("SELECT id FROM coding_run_events WHERE id=?", (event.id,)).fetchone()
            finally:
                conn.close()
        observations.append((event, identity))

    learning = SimpleNamespace(project_enabled=lambda project_id: True,
        observe_event=observe, automatic_reflection=False)
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda database, **kwargs: learning)
    saved = store.append_event(run.id, AgentEvent(AgentEventType.TOOL_CALL_ERROR, "s",
        tool_name="read_file", metadata={"failure_kind": "invalid_tool_arguments"}))
    assert observations[0][0].id == saved.id
    assert observations[0][1] == {"session_id": "s", "project_id": "p", "task_id": None,
                                  "provider": "configured", "model": "selected", "harness": "native"}


def test_collector_failure_never_loses_execution_evidence(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    store = CodingSessionStore(db)
    run = store.create_run("p", session_id="s")
    def unavailable(*args, **kwargs):
        raise RuntimeError("learning unavailable")
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", unavailable)
    event = store.append_event(run.id, AgentEvent(AgentEventType.ERROR, "s"))
    assert store.list_events(run.id)[0].id == event.id


def test_transcript_deltas_never_initialize_learning(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    store = CodingSessionStore(db)
    run = store.create_run("p", session_id="s")
    def forbidden(*args, **kwargs):
        pytest.fail("a transcript delta initialized learning")
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", forbidden)
    store.append_event(run.id, AgentEvent(AgentEventType.TEXT_DELTA, "s", content="private transcript"))
    store.append_event(run.id, AgentEvent(AgentEventType.TOOL_CALL_ARGUMENTS, "s", content="source bytes"))


def test_project_opt_out_stops_durable_observation(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    store = CodingSessionStore(db)
    run = store.create_run("p", session_id="s")
    learning = SimpleNamespace(project_enabled=lambda project_id: False,
        observe_event=lambda *args, **kwargs: pytest.fail("private project was observed"),
        automatic_reflection=True)
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda *args, **kwargs: learning)
    store.append_event(run.id, AgentEvent(AgentEventType.DONE, "s"))


def test_learned_memory_uses_existing_slice_inspector_and_project_boundary(tmp_path):
    db, _ = database(tmp_path)
    builder = ProjectContextBuilder(db.get_project("p"), db.get_settings("p"), [], [],
        total_token_budget=450, learned_memories=[learned(), learned("foreign", "other"),
            learned("procedure", kind="procedural", content="Do not duplicate a full skill body")])
    prompt = builder.assemble_system_prompt("Follow the user's instructions.")
    selected = [item for item in builder.get_context_items() if item["source"] == "learned_memory"]
    assert len(selected) == 1 and selected[0]["id"] == "learned_memory:lesson"
    assert selected[0]["included"] and selected[0]["reason"].startswith("Approved")
    assert "foreign" not in prompt and "full skill body" not in prompt
    assert approx_tokens(prompt) <= 450
    assert "packaging check" not in builder.get_request_parts()["trusted_system_prompt"]
    assert "packaging check" in builder.get_request_parts()["project_context"]


def test_coding_context_retrieval_remains_subordinate_to_explicit_rules(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    db.save_instruction(ProjectInstruction(id="rule", project_id="p", title="Test policy",
                                          content="Only run the project-authorized packaging checks."))
    retrievals = []
    def retrieve(query, **kwargs):
        retrievals.append((query, kwargs))
        return [learned()]
    learning = SimpleNamespace(project_enabled=lambda project_id: True, retrieve=retrieve)
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda *args, **kwargs: learning)
    prepared = ProjectCodingService(db).prepare_coding_prompt("p", "Check wheel packaging")
    assert retrievals and retrievals[0][1]["project_id"] == "p"
    prompt = prepared["project_context"]
    assert prompt.index("project-authorized") < prompt.index("packaging check after")
    source = next(item for item in prepared["context_inspector"]["sources"] if item["source"] == "learned_memory")
    assert source["included"] and source["reason"].startswith("Approved")


def test_zero_memory_setting_prevents_learned_retrieval(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    settings = db.get_settings("p")
    settings.token_budget["memory"] = 0
    db.save_settings(settings)
    learning = SimpleNamespace(project_enabled=lambda project_id: True,
        retrieve=lambda *args, **kwargs: pytest.fail("zero-budget memory queried"))
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda *args, **kwargs: learning)
    prepared = ProjectCodingService(db).prepare_coding_prompt("p", "Check packaging")
    assert not [item for item in prepared["context_items"] if item["source"] == "learned_memory"]


def test_private_chat_and_disabled_mode_do_not_open_learning_database(monkeypatch):
    agent = Agent.__new__(Agent)
    agent.config = {}
    assert agent._learning_event_observer(ChatSession(private=True)) is None
    assert agent._learning_event_observer(ChatSession(learning_enabled=False)) is None
    agent.config = {"self_improvement": {"mode": "off"}}
    assert agent._learning_event_observer(ChatSession()) is None


def test_ordinary_chat_observer_preserves_selected_model_and_no_auto_review(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    observations = []
    learning = SimpleNamespace(automatic_reflection=False,
        observe_event=lambda event, **kwargs: observations.append((event, kwargs)))
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda *args, **kwargs: learning)
    agent = Agent.__new__(Agent)
    agent.config = {}
    agent._learning_database = db
    provider = SimpleNamespace(name="existing-provider", current_model="chosen-model")
    agent.providers = SimpleNamespace(get_active=lambda: provider)
    observer = agent._learning_event_observer(ChatSession(session_id="s"))
    observer(AgentEvent(AgentEventType.TEXT_DELTA, "s", content="ignored"))
    observer(AgentEvent(AgentEventType.DONE, "s", content="Completion claim",
                        metadata={"completion_classification": "completed_unverified"}))
    assert len(observations) == 1
    assert observations[0][1]["model"] == "chosen-model"
    assert observations[0][1]["provider"] == "existing-provider"
    assert observations[0][0].metadata["completion_classification"] == "completed_unverified"
    assert provider.current_model == "chosen-model"


def test_ordinary_chat_retrieves_only_approved_global_data_with_token_cap(tmp_path, monkeypatch):
    db, _ = database(tmp_path)
    global_memory = learned("global", project_id=None, scope="global")
    learning = SimpleNamespace(settings=lambda: {"memory_token_budget": 200},
        retrieve=lambda *args, **kwargs: [global_memory, learned("project-local"),
            learned("unapproved", project_id=None, scope="global", user_approved=False)])
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda *args, **kwargs: learning)
    agent = Agent.__new__(Agent)
    agent.config = {}
    agent._learning_database = db
    selected = agent._learning_context_messages("packaging", ChatSession())
    assert len(selected) == 1 and selected[0].role == "user"
    assert "project-local" not in selected[0].content and "unapproved" not in selected[0].content
    assert approx_tokens(selected[0].content) <= 200
    assert agent.learning_context_inspector[0]["id"] == "global"
    assert agent._learning_context_messages("packaging", ChatSession(private=True)) == []


def test_approved_project_skill_is_discovered_progressively_and_exclusions_win(tmp_path, monkeypatch):
    from firik_agent.self_improvement.candidates import CandidateEngine
    db, root = database(tmp_path)
    engine = CandidateEngine(state_root=tmp_path / "learning-artifacts")
    payload = engine.prepare("skill", {"name": "project-packaging", "description": "Validate wheel packaging",
        "instructions": "Inspect the current project validation results after obtaining required approvals.",
        "evidence_ids": ["host-verification"], "scope": "project", "project_root": str(root)}, project_id="p")
    applied = engine.apply("skill", payload)
    learning = SimpleNamespace(project_enabled=lambda project_id: True, retrieve=lambda *args, **kwargs: [],
        project_skill_approvals=lambda project_id: [{"name": payload["name"], "target": applied["target"],
                                                     "after_sha256": applied["after_sha256"]}])
    monkeypatch.setattr("firik_agent.self_improvement.coordinator.coordinator_for", lambda *args, **kwargs: learning)
    coding = ProjectCodingService(db)
    catalogue = coding.prepare_coding_prompt("p", "Check the packaging")
    assert any(skill["name"] == "project-packaging" for skill in catalogue["skill_catalogue"])
    assert catalogue["selected_skills"] == []
    assert "Inspect the current project validation results" not in catalogue["project_context"]
    selected = coding.prepare_coding_prompt("p", "Check the packaging", selected_skills=["project-packaging"])
    assert selected["selected_skills"] == ["project-packaging"]
    assert "Inspect the current project validation results" in selected["project_context"]
    settings = db.get_settings("p")
    settings.coding_preferences["excluded_files"] = [".firik/skills/project-packaging/SKILL.md"]
    db.save_settings(settings)
    def must_not_read(*args, **kwargs):
        pytest.fail("excluded skill artifact was read")
    monkeypatch.setattr("firik_agent.self_improvement.candidates._read_files", must_not_read)
    excluded = coding.prepare_coding_prompt("p", "Check packaging", selected_skills=["project-packaging"])
    assert excluded["selected_skills"] == []
