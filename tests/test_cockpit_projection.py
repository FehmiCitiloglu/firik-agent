"""Replayed evidence must retain attribution and the limits of its validity."""
from firik_agent.projects.cockpit import CockpitState


def test_external_source_invalidation_updates_existing_evidence_without_new_test():
    state = CockpitState()
    state.start()
    state.apply({"type": "verification", "metadata": {"evidence_id": "test-1", "kind": "test",
                "executed": True, "passed": True, "fresh": True, "exit_code": 0}})
    state.apply({"type": "verification", "content": "Kaynak dışarıdan değişti.",
                "metadata": {"status": "stale", "workspace_revision": 1}})
    assert len(state.tests) == 1
    assert state.tests[0]["status"] == "stale" and state.tests[0]["fresh"] is False
    assert state.timeline[-1]["result"] == "Kaynak dışarıdan değişti."


def test_criterion_referencing_test_does_not_replace_test_or_invent_execution():
    state = CockpitState()
    state.apply({"type": "verification", "metadata": {"evidence_id": "test-1", "kind": "test",
                "passed": True, "exit_code": 0, "command": "pytest"}})
    state.apply({"type": "verification", "metadata": {"evidence_id": "test-1", "kind": "criterion",
                "criterion": "Import works", "status": "passed"}})
    state.apply({"type": "verification", "metadata": {"kind": "criterion", "criterion": "Remote API works",
                "status": "unverified"}})
    assert len(state.tests) == 3
    assert state.tests[0]["kind"] == "test" and state.tests[0]["command"] == "pytest"
    assert state.tests[1]["status"] == "passed"
    assert state.tests[2]["status"] == "unexecuted" and "exit_code" not in state.tests[2]


def test_same_request_usage_frames_replace_and_child_tool_ids_are_separate():
    state = CockpitState()
    state.start()
    for count in (3, 7):
        state.apply({"type": "model_usage", "metadata": {"iteration": 1, "usage": {"total_tokens": count}}})
    state.apply({"type": "model_usage", "metadata": {"agent_id": "child", "iteration": 1,
                "usage": {"total_tokens": 5}}})
    for owner in ("orchestrator", "child"):
        state.apply({"type": "tool_call_started", "tool_name": "run_command", "arguments": {"command": owner},
                    "metadata": {"agent_id": owner, "tool_call_id": "same-id"}})
    state.apply({"type": "tool_call_result", "tool_name": "run_command", "content": 'Exit code 2:\nfailed',
                "metadata": {"agent_id": "child", "tool_call_id": "same-id"}})
    assert state.terminal[0]["status"] == "running"
    assert state.terminal[1]["status"] == "failed" and state.terminal[1]["exit_code"] == 2
    state.finish("blocked")
    assert state.summary["usage"]["total_tokens"] == 12 and state.summary["cost"] is None


def test_gate_enrichment_keeps_test_identity_and_marks_in_run_mutation_stale():
    state = CockpitState()
    state.apply({"type": "verification", "metadata": {"evidence_id": "test-1", "kind": "test",
                "passed": True, "fresh": False, "status": "stale", "exit_code": 0}})
    state.apply({"type": "quality_gate", "metadata": {"evidence_id": "test-1", "gate_id": "lint-1", "gate_kind": "lint"}})
    assert len(state.tests) == 1
    assert state.tests[0]["status"] == "stale" and state.tests[0]["gate_kind"] == "lint"


def test_mcp_failures_are_observable_and_blocked_cannot_be_replaced_by_done():
    state = CockpitState()
    state.apply({"type": "mcp_error", "server_name": "local-tools", "content": "Connection refused"})
    assert state.timeline[-1]["server"] == "local-tools"
    assert state.timeline[-1]["error"] == "Connection refused"
    state.apply({"type": "task_state", "metadata": {"state": "blocked"}})
    state.apply({"type": "done", "content": "Model claims success"})
    assert state.agents["orchestrator"]["status"] == "blocked"


def test_malformed_historical_agent_ids_cannot_grow_usage_maps_without_bound():
    state = CockpitState()
    state.start()
    for index in range(600):
        state.apply({"type": "model_request_started", "metadata": {"agent_id": f"child-{index}", "iteration": 1}})
        state.apply({"type": "model_usage", "metadata": {"agent_id": f"child-{index}", "iteration": 1,
                    "usage": {"total_tokens": 1}}})
    assert len(state.agents) == len(state._iterations) == len(state._requests) == 17
