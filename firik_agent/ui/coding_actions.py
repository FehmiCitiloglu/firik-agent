"""Cockpit actions share the existing run control, tool gate and event journal."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.types import ToolCall
from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
from firik_agent.projects.model_capabilities import model_capabilities


class CodingActions:
    def _coding_metadata(self, provider, model):
        config = self.config.get("providers", {}).get(provider, {})
        rows = (config.get("models") or {}) if isinstance(config, dict) else {}
        metadata = rows.get(model) if isinstance(rows, dict) else next(
            (item for item in rows if isinstance(item, dict) and item.get("id") == model), None)
        if isinstance(metadata, dict) and (metadata.get("verified") is True
                or metadata.get("source") in {"provider", "verified_probe"}):
            return deepcopy(metadata)
        if provider == self.provider and self._model_metadata_url == self.base_url:
            cached = self._model_metadata.get(model)
            if isinstance(cached, dict):
                return {**deepcopy(cached), "source": "provider"}
        return {}

    def _coding_worker(self, provider=None, model=None):
        """Resolve only configured providers; no model-supplied endpoints."""
        config = deepcopy(self.config)
        config["mcp_servers"] = {}
        config["plugins"] = {"autoload_builtins": False, "directory": ""}
        config["skills"] = {"active_skills": []}
        config["computer_use"] = {"enabled": False}
        worker = Agent(config=config, config_path=str(self.config_path) if self.config_path else None)
        worker._is_initialized = True
        provider = provider or self.provider
        worker.providers.set_active(provider)
        selected = worker.providers.get_active()
        selected.set_model(model or selected.current_model)
        self.configure_model_metadata(selected)
        metadata = self._coding_metadata(provider, selected.current_model)
        if metadata:
            selected.model_metadata[selected.current_model] = metadata
        caps = model_capabilities(metadata)
        worker.subagent_prices = {"input_per_million": caps.get("input_cost_per_million"),
                                 "output_per_million": caps.get("output_cost_per_million")}
        return worker

    def _coding_action_project(self, project_id):
        project = self.projects.db.get_project(project_id)
        if project is None:
            raise ValueError("Proje bulunamadı.")
        return project

    def _coding_session_project(self, session_id):
        project_id = self._coding_session_projects.get(session_id)
        if not project_id:
            tool_filter = self._coding_filters.get(session_id)
            project_id = tool_filter.control.project_id if tool_filter else None
        if not project_id:
            raise ValueError("Kodlama oturumu bu projeyle ilişkilendirilmedi.")
        return self._coding_action_project(project_id)

    def _coding_background(self, function, on_success, on_error):
        async def run():
            return await asyncio.to_thread(function)
        return self.runtime.submit(run(), on_done=lambda result: self._dispatch(on_success, result),
            on_error=lambda error: self._dispatch(on_error, self._coding_redact(str(error))))

    def list_coding_checkpoints_async(self, project_id, on_success, on_error, run_id=None):
        self._coding_action_project(project_id)
        return self._coding_background(lambda: self.checkpoint_store.list_checkpoints(project_id, run_id=run_id), on_success, on_error)

    def create_coding_checkpoint_async(self, project_id, on_success, on_error, label="", run_id=None):
        project = self._coding_action_project(project_id)
        return self._coding_background(lambda: self.checkpoint_store.create_checkpoint(
            project_id, project.local_path, label=label, run_id=run_id), on_success, on_error)

    def compare_coding_checkpoint_async(self, project_id, checkpoint_id, on_success, on_error, run_id=None):
        project = self._coding_action_project(project_id)
        return self._coding_background(lambda: self.checkpoint_store.compare_checkpoint(
            checkpoint_id, project_id=project_id, root=project.local_path, run_id=run_id), on_success, on_error)

    def restore_coding_checkpoint_async(self, project_id, checkpoint_id, expected_hashes, on_success, on_error, run_id=None):
        project = self._coding_action_project(project_id)
        def restore():
            if any(control.project_id == project_id for control in self._coding_controls.values()):
                raise ValueError("Geri yüklemeden önce bu projenin çalışan ajanını durdurun.")
            return self.checkpoint_store.restore_checkpoint(checkpoint_id, project_id=project_id,
                root=project.local_path, expected_hashes=expected_hashes, run_id=run_id)
        return self._coding_background(restore, on_success, on_error)

    def review_coding_change_async(self, project_id, checkpoint_id, path, decision, revision, on_success, on_error,
                                  hunk_id=None, run_id=None):
        project = self._coding_action_project(project_id)
        def review():
            if any(control.project_id == project_id for control in self._coding_controls.values()):
                raise ValueError("Değişiklik incelemesini uygulamadan önce çalışan ajanı durdurun.")
            return self.checkpoint_store.review_change(checkpoint_id, project_id=project_id, root=project.local_path,
                path=path, decision=decision, revision=revision, hunk_id=hunk_id, run_id=run_id)
        return self._coding_background(review, on_success, on_error)

    def coding_model_info_async(self, project_id, model, provider, on_success, on_error, probe=False):
        self._coding_action_project(project_id)
        async def run():
            metadata = self._coding_metadata(provider, model)
            worker = self._coding_worker(provider, model)
            endpoint = worker.providers.get_active()
            capabilities = model_capabilities(metadata)
            result = {"model": model, "provider": provider, "local": endpoint.is_local,
                      "capabilities": capabilities, "source": capabilities.get("source"),
                      "health": {"status": "not_probed", "label": "Bağlantı henüz sınanmadı"}}
            if probe:
                result["health"] = await endpoint.endpoint_health()
            return self._coding_redact(result)
        return self.runtime.submit(run(), on_done=lambda result: self._dispatch(on_success, result),
            on_error=lambda error: self._dispatch(on_error, self._coding_redact(str(error))))

    def _coding_manual_async(self, session_id, task, operation, on_success, on_error, on_event=None):
        """An explicit terminal/agent action is a durable run with visible approvals."""
        project = self._coding_session_project(session_id)
        cancel_event = threading.Event()
        entered = threading.Event()
        with self._stream_lock:
            if session_id in self._streams:
                self._dispatch(on_error, "Bu oturumda bir çalışma zaten yürütülüyor.")
                return None
            self._streams[session_id] = cancel_event
        async def run():
            entered.set()
            control = manager = record = None
            started = time.monotonic()
            status, result, error_text = "completed", None, ""
            commands = []
            child_decisions, usage_requests = [], {}
            try:
                settings = self.projects.db.get_settings(project.id)
                prefs = settings.coding_preferences
                session = self.get_or_create_session(session_id)
                previous = self.coding_store.latest_run(project.id, session_id=session_id)
                control = CodingRunControl(session_id, project.id, previous.task_id if previous else None,
                    mode=prefs.get("mode", "approved"), permissions=prefs.get("permissions"), policy=prefs.get("policy"),
                    excluded_paths=prefs.get("excluded_files", []))
                # Legacy project denials are inherited, including broad shell access.
                denies = {"never", "deny"}
                mapping = {"read_files": ["allow_reads"], "write_files": ["allow_edits"],
                           "delete_files": ["allow_deletes"], "terminal": ["allow_terminal", "allow_tests"],
                           "network_access": ["allow_terminal", "allow_tests", "allow_mcp"],
                           "install_packages": ["allow_terminal", "allow_tests"]}
                for key, permissions in mapping.items():
                    if settings.permissions.get(key) in denies:
                        control.permissions.update({permission: False for permission in permissions})
                provider = prefs.get("provider", self.provider)
                control.capabilities = model_capabilities(self._coding_metadata(provider, session.model))
                record = self.coding_store.create_run(project.id, session_id=session_id, run_id=control.run_id,
                    task_id=control.task_id, provider=provider, model=session.model, mode=control.mode,
                    permissions={**control.permissions, **control.policy_snapshot()}, user_request=task, title=task[:120])
                self._coding_controls[session_id] = control
                def receive(event):
                    try:
                        self.coding_store.append_event(record.id, event)
                    except Exception:
                        cancel_event.set()
                        control.cancel_event.set()
                        raise
                    if event.type == AgentEventType.SUBAGENT_EVENT:
                        detail = event.metadata
                        if detail.get("child_event_type") == "model_usage":
                            usage_requests[(detail["agent_id"], detail.get("request_iteration", 1))] = detail.get("child_metadata", {}).get("usage", {})
                        if detail.get("status") not in {"created", "queued", "running"} and not detail.get("child_event_type"):
                            child_decisions.append({"kind": "subagent", **detail, "result": event.content})
                    if on_event:
                        self._dispatch(on_event, event)
                control.on_event = receive
                tools = CodingToolFilter(project.local_path, control)
                self._coding_filters[session_id] = tools
                checkpoint = await asyncio.to_thread(self.checkpoint_store.create_checkpoint,
                    project.id, project.local_path, run_id=record.id, label=task[:120], kind="before_action")
                control.emit(AgentEventType.CHECKPOINT_CREATED, "Çalışma öncesi kontrol noktası kaydedildi.", checkpoint=checkpoint)
                tools.before_file_mutation = lambda path: self._checkpoint_before_edit(project.id, project.local_path, record.id, path)
                if prefs.get("quality_gates") is not None:
                    tools.configure_quality_gates(prefs["quality_gates"])
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                def emit_tool(kind, name, arguments, call_id, content="", **metadata):
                    receive(AgentEvent(kind, session_id, project_id=project.id, task_id=control.task_id,
                        tool_name=name, arguments=self._coding_redact(arguments), content=self._coding_redact(content),
                        metadata={"project_id": project.id, "task_id": control.task_id,
                                  "run_id": record.id, "tool_call_id": call_id, **metadata}))
                result, manager = await operation(tools, control, session, receive, emit_tool, prefs)
                if isinstance(result, dict) and result.get("command"):
                    commands.append(result["command"])
                if isinstance(result, dict) and result.get("status") == "cancelled":
                    status = "cancelled"
                elif isinstance(result, dict) and (result.get("passed") is False or result.get("status") == "failed"):
                    status = "failed"
                if status == "completed" and control.changes:
                    completion = await tools.completion_gate_async()
                    if not completion["passed"]:
                        status = "blocked"
                        result = {**(result if isinstance(result, dict) else {}), "status": "blocked",
                                  "passed": False, "reasons": completion["reasons"]}
                        control.set_state("blocked", "\n".join(completion["reasons"]))
                return result
            except asyncio.CancelledError:
                status = "cancelled"
                return {"status": "cancelled", "output": "İşlem durduruldu."}
            except Exception as error:
                status = "failed"
                error_text = self._coding_redact(str(error))
                if control:
                    control.emit(AgentEventType.ERROR, error_text, operation=task)
                raise
            finally:
                close_error = None
                try:
                    try:
                        if manager:
                            await manager.close()
                    except Exception as error:  # noqa: BLE001 -- cleanup failures cannot leave active registries behind
                        close_error = error
                        status, error_text = "failed", self._coding_redact(str(error))
                    if record and control:
                        snapshot = control.snapshot()
                        usage = {}
                        for values in usage_requests.values():
                            for key, value in values.items():
                                if isinstance(value, (int, float)) and not isinstance(value, bool):
                                    usage[key] = usage.get(key, 0) + value
                        self.coding_store.update_run(record.id, status=status, result=status,
                            ended_at=datetime.now(timezone.utc).isoformat(), duration_sec=int(time.monotonic() - started),
                            tests_executed=snapshot["tests"], file_journal=snapshot["changes"], commands_executed=commands,
                            decisions=snapshot["decisions"] + child_decisions, summary=task, error=error_text, usage=usage,
                            tokens_used=int(usage.get("total_tokens", 0)),
                            checkpoints=[{"kind": "policy", "policy": snapshot["policy"], "capabilities": control.capabilities}])
                finally:
                    self._coding_managers.pop(session_id, None)
                    if self._coding_controls.get(session_id) is control:
                        self._coding_controls.pop(session_id, None)
                    with self._stream_lock:
                        if self._streams.get(session_id) is cancel_event:
                            self._streams.pop(session_id, None)
                            self._stream_futures.pop(session_id, None)
                if close_error:
                    raise close_error
        coroutine = run()
        try:
            future = self.runtime.submit(coroutine, on_done=lambda value: self._dispatch(on_success, value),
                on_error=lambda error: self._dispatch(on_error, self._coding_redact(str(error))))
        except Exception:
            coroutine.close()
            with self._stream_lock:
                if self._streams.get(session_id) is cancel_event:
                    self._streams.pop(session_id, None)
                    self._stream_futures.pop(session_id, None)
            raise
        with self._stream_lock:
            if self._streams.get(session_id) is cancel_event:
                self._stream_futures[session_id] = future
        def completed_before_entry(future):
            if not entered.is_set():
                with self._stream_lock:
                    if self._streams.get(session_id) is cancel_event:
                        self._streams.pop(session_id, None)
                        self._stream_futures.pop(session_id, None)
        future.add_done_callback(completed_before_entry)
        return future

    def run_coding_command_async(self, session_id, command, on_success, on_error, kind="shell", cwd=".", on_event=None):
        if kind not in {"shell", "test"}:
            raise ValueError("Komut türü shell veya test olmalı.")
        async def operation(tools, control, session, receive, emit, prefs):
            name = "run_tests" if kind == "test" else "run_command"
            call_id = "manual_" + uuid4().hex
            args = {"command": command, "cwd": cwd}
            emit(AgentEventType.TOOL_CALL_STARTED, name, args, call_id)
            result = await tools.execute(ToolCall(id=call_id, function={"name": name,
                "arguments": json.dumps(args)}), session)
            emit(AgentEventType.TOOL_CALL_ERROR if result.is_error else AgentEventType.TOOL_CALL_RESULT,
                 name, args, call_id, result.content, is_error=result.is_error)
            try:
                payload = json.loads(result.content)
            except ValueError:
                payload = {"command": command, "output": result.content, "passed": not result.is_error}
            if isinstance(payload, dict):
                payload["status"] = "failed" if result.is_error or payload.get("exit_code", 0) != 0 else "completed"
            return payload, None
        return self._coding_manual_async(session_id, command, operation, on_success, on_error, on_event)

    def run_coding_quality_gates_async(self, session_id, on_success, on_error, on_event=None, gates=None):
        async def operation(tools, control, session, receive, emit, prefs):
            specifications = gates if gates is not None else prefs.get("quality_gates")
            if specifications is None:
                tools.discover_quality_gates()
            else:
                tools.configure_quality_gates(specifications)
            results = []
            for gate in control.quality_gates:
                if gate["enabled"]:
                    value = await tools.run_quality_gate(gate["id"])
                    results.append(json.loads(value.content))
            passed = all(r.get("passed") for r in results) if results else None
            return {"results": results, "passed": passed,
                    "executed": bool(results), "status": "completed" if passed else "failed" if results else "unexecuted"}, None
        return self._coding_manual_async(session_id, "Kalite kapılarını çalıştır", operation, on_success, on_error, on_event)

    def spawn_coding_agent_async(self, session_id, role, task, on_success, on_error, model=None,
                                provider=None, write_paths=None, on_event=None):
        async def start(manager):
            return await manager.spawn(role, task, provider=provider, model=model, write_paths=write_paths)
        manager = self._coding_managers.get(session_id)
        if manager:
            return self.runtime.submit(start(manager), on_done=lambda result: self._dispatch(on_success, result),
                on_error=lambda error: self._dispatch(on_error, self._coding_redact(str(error))))
        async def operation(tools, control, session, receive, emit, prefs):
            from firik_agent.projects.subagents import SubagentToolFilter
            selected = prefs.get("provider", self.provider)
            manager = SubagentToolFilter(tools, session, lambda: self._coding_worker(selected, session.model), receive,
                self._coding_redact, provider_resolver=lambda p, m: self._coding_worker(p or selected, m or session.model),
                limits=prefs.get("subagent_limits"))
            self._coding_managers[session_id] = manager
            try:
                job = await start(manager)
                result = await manager.wait(job["agent_id"])
                return result, manager
            except BaseException:
                await manager.close()
                raise
        return self._coding_manual_async(session_id, f"{role}: {task}", operation, on_success, on_error, on_event)

    def steer_coding_agent_async(self, session_id, agent_id, message, on_success, on_error):
        manager = self._coding_managers.get(session_id)
        if agent_id in {"orchestrator", "parent"}:
            control = self._coding_controls.get(session_id)
            if not control:
                self._dispatch(on_error, "Yönlendirilecek çalışan ajan yok.")
                return None
            queue = self._coding_steering.setdefault(session_id, [])
            with self._stream_lock:
                if len(queue) >= 32:
                    self._dispatch(on_error, "Bekleyen yönlendirme sınırına ulaşıldı.")
                    return None
                queue.append(self._coding_redact(message[:8000]))
            control.emit(AgentEventType.AGENT_MESSAGE, "Kullanıcı yönlendirmesi sonraki model isteğinde teslim edilecek.",
                         sender="user", receiver="orchestrator", message=self._coding_redact(message[:8000]))
            self._dispatch(on_success, {"status": "queued"})
            return None
        if not manager:
            self._dispatch(on_error, "Yönlendirilecek alt ajan bulunamadı.")
            return None
        return self.runtime.submit(manager.steer(agent_id, message),
            on_done=lambda result: self._dispatch(on_success, result),
            on_error=lambda error: self._dispatch(on_error, self._coding_redact(str(error))))

    def cancel_coding_agent_async(self, session_id, agent_id, on_success, on_error):
        manager = self._coding_managers.get(session_id)
        if not manager:
            self._dispatch(on_error, "Durdurulacak alt ajan bulunamadı.")
            return None
        return self.runtime.submit(manager.cancel(agent_id), on_done=lambda result: self._dispatch(on_success, result),
            on_error=lambda error: self._dispatch(on_error, self._coding_redact(str(error))))

    def _checkpoint_before_edit(self, project_id, root, run_id, path):
        point = self.checkpoint_store.create_checkpoint(project_id, root, run_id=run_id,
            label=f"Düzenleme öncesi: {path}", kind="before_edit", paths=[path])
        if point.get("skipped") or point.get("limited"):
            raise PermissionError("Dosya için geri yüklenebilir kontrol noktası alınamadı; düzenleme yapılmadı.")
        return point

    def suggest_coding_composer_async(self, project_id, query, on_success, on_error, **options):
        return self._coding_background(lambda: self.projects.coding.suggest_composer(project_id, query, **options),
                                      on_success, on_error)
