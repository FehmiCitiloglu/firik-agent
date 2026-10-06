"""Kodlama oturumlarını GUI dışındaki kalıcı ve izinli yürütücüye bağlar."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import dataclasses
import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
from uuid import uuid4

from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEventType
from firik_agent.core.session import ChatSession
from firik_agent.core.types import Message
from firik_agent.mcp.models import redact_secrets
from firik_agent.ui.coding_actions import CodingActions

logger = logging.getLogger(__name__)


class CodingBridge(CodingActions):
    def _init_coding_bridge(self):
        from firik_agent.projects.coding_sessions import CodingSessionStore
        def configured_secrets(value):
            if not isinstance(value, dict):
                return []
            found = []
            for name, item in value.items():
                if isinstance(item, str) and re.search(r"(?i)(api.?key|password|secret|token)$", name):
                    found.append(item)
                elif isinstance(item, dict):
                    found.extend(configured_secrets(item))
            return found
        self._coding_known_secrets = tuple(configured_secrets(self.config.get("providers", {})))
        self.coding_store = CodingSessionStore(self.projects.db, known_secrets=self._coding_known_secrets)
        self.coding_store.recover_interrupted()
        self._coding_controls = {}
        self._coding_filters = {}
        self._coding_managers = {}
        self._coding_session_projects = {}
        self._coding_steering = {}
        from firik_agent.projects.checkpoints import CheckpointStore
        self.checkpoint_store = CheckpointStore(self.projects.db, known_secrets=self._coding_known_secrets)
        from firik_agent.projects.git_state import GitStateStore
        self.git_state = GitStateStore()

    def prepare_coding_async(self, project_id, prompt, on_success, on_error, **options):
        def prepare_context():
            prepared_options = dict(options)
            provider = prepared_options.pop("provider", None)
            model = prepared_options.pop("model", None)
            if "model_metadata" not in prepared_options:
                settings = self.projects.db.get_settings(project_id)
                selected_provider = provider or settings.coding_preferences.get("provider") or self.provider
                session = self.sessions.get(options.get("session_id"))
                selected_model = model or (session.model if session is not None else settings.coding_model)
                metadata = self._coding_metadata(selected_provider, selected_model)
                # Only explicit config evidence is accepted. ModelInfo defaults
                # and model-name guesses cannot establish a capability.
                if isinstance(metadata, dict):
                    source = metadata.get("source") or metadata.get("capability_source")
                    if metadata.get("verified") is True or source in {"provider", "verified_probe"}:
                        prepared_options["model_metadata"] = deepcopy(metadata)
            if "available_skills" not in prepared_options:
                prepared_options["available_skills"] = self._coding_skill_definitions(project_id, options.get("session_id"))
            return self.projects.coding.prepare_coding_prompt(project_id, self._coding_redact(prompt), **prepared_options)
        async def prepare():
            return await asyncio.to_thread(prepare_context)
        return self.runtime.submit(prepare(), on_done=lambda result: self._dispatch(on_success, result),
                                   on_error=lambda error: self._dispatch(on_error, str(error)))

    def restore_coding(self, project_id, run_id=None):
        record = (self.coding_store.get_run(run_id, project_id=project_id) if run_id
                  else self.coding_store.latest_run(project_id))
        if run_id and record is None:
            raise ValueError("Oturum bu projeye ait değil veya bulunamadı.")
        if record is None:
            session = ChatSession(model=self.default_model, active_mcp_servers=set())
        else:
            session = ChatSession(session_id=record.session_id, model=record.model or self.default_model,
                                  active_mcp_servers=set())
            # Exclude half-written tool protocol groups. The durable event log
            # retains them; resumed inference starts from conversational turns.
            session.messages = [Message.model_validate(self._coding_redact(m)) for m in record.messages
                                if m.get("role") in {"user", "assistant"} and not m.get("tool_calls")]
        self.sessions[session.session_id] = session
        if not hasattr(self, "_coding_session_projects"):
            self._coding_session_projects = {}
        self._coding_session_projects[session.session_id] = project_id
        return session, record

    def coding_control(self, session_id, action, request_id=None, allowed=False):
        control = self._coding_controls.get(session_id)
        if control is None:
            return False
        if action == "approve":
            return control.decide_approval(request_id, allowed)
        if action == "pause":
            control.pause()
        elif action == "resume":
            control.resume()
        elif action == "cancel":
            control.cancel()
            self.cancel_active_stream(session_id)
        else:
            raise ValueError("Bilinmeyen ajan kontrolü.")
        return True

    def undo_coding_async(self, session_id, path, on_success, on_error):
        tool_filter = self._coding_filters.get(session_id)
        if tool_filter is None:
            self._dispatch(on_error, "Bu çalışma için güvenli geri alma kaydı bulunamadı.")
            return
        async def undo():
            result = await asyncio.to_thread(tool_filter.undo_file, path)
            snapshot = tool_filter.control.snapshot()
            self.coding_store.update_run(tool_filter.control.run_id, file_journal=snapshot.get("changes", []))
            self.coding_store.record_decision(tool_filter.control.run_id, {"kind": "undo", "path": path, "approved": True})
            self.coding_store.checkpoint(tool_filter.control.run_id, {"kind": "undo", **result})
            return result
        return self.runtime.submit(undo(), on_done=lambda result: self._dispatch(on_success, result),
                                   on_error=lambda error: self._dispatch(on_error, str(error)))

    def _coding_stream_async(self, session_id, prompt, cwd, on_event, on_done, on_error, options):
        from firik_agent.projects.coding_runtime import CodingRunControl, CodingToolFilter
        from firik_agent.projects.coding_sessions import ACTIVE_STATUSES, TERMINAL_STATUSES
        if not cwd:
            raise ValueError("Kodlama çalışması için proje klasörü gerekli.")
        project_id = options.get("project_id")
        if not project_id:
            raise ValueError("Kodlama çalışması için proje kimliği gerekli.")
        options = deepcopy(options)
        task_id = options.get("task_id")
        cancel_event = threading.Event()
        with self._stream_lock:
            if session_id in self._streams:
                raise ValueError("Bu oturumda bir çalışma zaten yürütülüyor.")
            self._streams[session_id] = cancel_event
        run_id = "run_" + uuid4().hex
        started = time.monotonic()
        entered, notified = threading.Event(), threading.Event()
        control = session = record = None
        subagents = None
        initial_system_prompt = None
        acquired = set()
        terminal_events = []
        checkpoints, decisions, usage_requests, commands = [], [], {}, []
        state = {"last_checkpoint": 0.0, "partial": "", "partial_start": 0,
                 "iteration": 0, "terminal_status": None, "prior_count": 0,
                 "initial_messages": []}

        def release_stream():
            with self._stream_lock:
                if self._streams.get(session_id) is cancel_event:
                    self._streams.pop(session_id, None)
                    self._stream_futures.pop(session_id, None)
                if control is not None and self._coding_controls.get(session_id) is control:
                    self._coding_controls.pop(session_id, None)
                self._coding_managers.pop(session_id, None)
                self._coding_steering.pop(session_id, None)

        def notify(status, final):
            if notified.is_set():
                return
            notified.set()
            # No terminal event can enable Send before persistence and cleanup.
            for event in terminal_events:
                if on_event:
                    self._dispatch(on_event, event)
            callback = on_error if status == "failed" and on_error else on_done
            if callback:
                self._dispatch(callback, self._coding_redact(final))

        def snapshot_messages():
            messages = [m.model_dump(exclude_none=True) for m in session.messages]
            if len(messages) <= state["prior_count"]:
                messages = list(state["initial_messages"])
            if state["partial"] and len(messages) <= state["partial_start"]:
                messages.append({"role": "assistant", "content": state["partial"], "partial": True})
            return self._coding_redact(messages)

        def persist_snapshot(status=None, final=None, force=False):
            if record is None or control is None:
                return
            now = time.monotonic()
            if not force and now - state["last_checkpoint"] < 1.0:
                return
            snapshot = control.snapshot()
            checkpoint = {"kind": "progress", "created_at": time.time(), "run_id": run_id,
                          "project_id": project_id, "task_id": task_id, "session_id": session_id,
                          "state": snapshot["state"],
                          "completed_step_ids": [step.get("id") for step in snapshot.get("plan", []) if step.get("status") == "completed"],
                          "file_hashes": [{"path": change.get("path"), "after_hash": change.get("after_hash"), "undone": change.get("undone", False)}
                                          for change in snapshot.get("changes", [])],
                          "test_evidence_ids": [test.get("evidence_id") for test in snapshot.get("tests", [])],
                          "pending_approvals": snapshot.get("pending_approvals", {})}
            checkpoints.append(checkpoint)
            if len(checkpoints) > 200:
                del checkpoints[:-200]
            current_status = status or state["terminal_status"] or snapshot["state"]
            if current_status == "implementing":
                current_status = "applying"
            if current_status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
                current_status = "running"
            usage = {}
            for values in usage_requests.values():
                for key, value in values.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        usage[key] = usage.get(key, 0) + value
                    else:
                        usage[key] = value
            fields = dict(status=current_status, messages=snapshot_messages(),
                          duration_sec=int(now - started), plan=snapshot.get("plan", []),
                          context_report=options.get("visibility_report", []),
                          checkpoints=checkpoints, file_journal=snapshot.get("changes", []),
                          tests_executed=snapshot.get("tests", []), decisions=decisions,
                          completed_steps=[step for step in snapshot.get("plan", []) if step.get("status") == "completed"],
                          files_changed=list(dict.fromkeys(change["path"] for change in snapshot.get("changes", []) if change.get("path"))),
                          commands_executed=commands, usage=usage,
                          tokens_used=int(usage.get("total_tokens", usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0))))
            if final is not None:
                fields.update(result=current_status, summary=final,
                              error=final if current_status == "failed" else "")
            self.coding_store.update_run(run_id, **fields)
            state["last_checkpoint"] = now

        def receive(event):
            if event.session_id != session_id:
                raise ValueError("Ajan olayı yanlış oturuma ait.")
            expected = {"project_id": project_id, "task_id": task_id, "run_id": run_id}
            for key, value in expected.items():
                existing = event.metadata.get(key, value)
                if existing != value:
                    raise ValueError("Ajan olayı yanlış proje, görev veya çalışmaya ait.")
            if event.project_id not in {None, project_id} or event.task_id not in {None, task_id}:
                raise ValueError("Ajan olayı yanlış proje veya göreve ait.")
            safe = dataclasses.replace(event, project_id=project_id, task_id=task_id,
                                       content=self._coding_redact(event.content or ""),
                                       arguments=self._coding_redact(event.arguments),
                                       metadata=self._coding_redact({**event.metadata, **expected}))
            try:
                self.coding_store.append_event(run_id, safe)
                if event.type == AgentEventType.SUBAGENT_EVENT:
                    child = safe.metadata
                    if child.get("child_event_type") == "model_usage":
                        detail = child.get("child_metadata", {})
                        usage_requests[(child["agent_id"], detail.get("iteration", child.get("request_iteration", 1)))] = detail.get("usage", {})
                    if child.get("status") != "running" and not child.get("child_event_type"):
                        decisions.append({"kind": "subagent", **child, "result": safe.content})
                    persist_snapshot(force=not child.get("child_event_type"))
                    if on_event:
                        self._dispatch(on_event, safe)
                    return
                first_delta = event.type == AgentEventType.TEXT_DELTA and not state["partial"]
                if event.type == AgentEventType.MODEL_REQUEST_STARTED:
                    state.update(partial="", partial_start=len(session.messages),
                                 iteration=event.metadata.get("iteration", state["iteration"] + 1))
                elif event.type == AgentEventType.TEXT_DELTA:
                    state["partial"] += safe.content or ""
                elif event.type == AgentEventType.MODEL_USAGE:
                    from firik_agent.projects.model_capabilities import estimate_usage_cost
                    usage = dict(safe.metadata.get("usage", {}))
                    cost = estimate_usage_cost(usage, control.capabilities)
                    if cost.get("cost") is not None:
                        usage["cost"] = cost["cost"]
                    usage_requests[state["iteration"]] = usage
                elif event.type == AgentEventType.APPROVAL_DECIDED:
                    decisions.append({"kind": "approval", **safe.metadata})
                elif event.type in {AgentEventType.TOOL_CALL_RESULT, AgentEventType.TOOL_CALL_ERROR}:
                    try:
                        result = json.loads(safe.content)
                    except (TypeError, ValueError):
                        result = {}
                    if isinstance(result, dict) and result.get("command"):
                        commands.append(result["command"])
                persist_snapshot(force=first_delta or event.type not in {AgentEventType.TEXT_DELTA, AgentEventType.REASONING_DELTA,
                                                                         AgentEventType.TOOL_CALL_ARGUMENTS})
            except Exception:
                cancel_event.set()
                control.cancel_event.set()
                raise
            if event.type in {AgentEventType.DONE, AgentEventType.ERROR}:
                terminal_events.append(safe)
            elif on_event:
                self._dispatch(on_event, safe)

        async def run():
            nonlocal control, session, record, subagents, initial_system_prompt
            entered.set()
            status, final = "completed", ""
            try:
                project = self.projects.db.get_project(project_id)
                if project is None or Path(cwd).resolve() != Path(project.local_path).resolve():
                    raise ValueError("Çalışma klasörü seçili projeyle eşleşmiyor.")
                session = self.get_or_create_session(session_id)
                session.system_prompt = self._coding_redact(session.system_prompt)
                initial_system_prompt = session.system_prompt
                session.messages = [Message.model_validate(self._coding_redact(m.model_dump(exclude_none=True))) for m in session.messages]
                safe_prompt = self._coding_redact(prompt)
                state["prior_count"] = len(session.messages)
                state["initial_messages"] = [m.model_dump(exclude_none=True) for m in session.messages] + [{"role": "user", "content": safe_prompt}]
                previous = self.coding_store.latest_run(project_id, session_id=session_id)
                continuation = previous if previous and previous.status in {"interrupted", "failed", "cancelled", "blocked"} else None
                legacy = self.projects.db.get_settings(project_id).permissions
                permissions = dict(options.get("permissions") or {})
                denied = {"never", "deny"}
                for old, capabilities in {
                    "read_files": ("allow_reads",), "write_files": ("allow_edits",),
                    "delete_files": ("allow_deletes",), "terminal": ("allow_terminal", "allow_tests"),
                    "network_access": ("allow_terminal", "allow_tests", "allow_mcp"),
                    "install_packages": ("allow_terminal", "allow_tests"),
                }.items():
                    if legacy.get(old) in denied:
                        permissions.update({capability: False for capability in capabilities})
                control = CodingRunControl(session_id=session_id, project_id=project_id, task_id=task_id,
                                           mode=options.get("mode", "approved"), permissions=permissions,
                                           run_id=run_id, policy=options.get("policy"),
                                           excluded_paths=options.get("excluded_paths", []))
                control.capabilities = dict(options.get("capabilities", {}))
                control.visibility_report = options.get("visibility_report", [])
                control.context_items = options.get("context_items", [])
                control.excluded_paths = options.get("excluded_paths", [])
                def drain_messages(target):
                    with self._stream_lock:
                        pending = self._coding_steering.pop(session_id, [])
                    for message in pending:
                        target.messages.append(Message(role="user", content=message))
                    manager = self._coding_managers.get(session_id)
                    if manager:
                        manager.drain_parent_messages(target)
                control.drain_pending_messages = drain_messages
                for index, step in enumerate(options.get("previous_plan", [])[:50]):
                    if isinstance(step, dict) and step.get("title"):
                        control.plan.append(self._coding_redact({**step, "id": step.get("id", f"step_{index + 1}"),
                            "status": step.get("status") if step.get("status") in {"pending", "in_progress"} else "pending",
                            "evidence_ids": []}))
                record = self.coding_store.create_run(
                    project_id, task_id=task_id, session_id=session_id, run_id=run_id,
                    provider=options.get("provider", self.provider), model=session.model, mode=control.mode,
                    permissions={**control.permissions, **control.policy_snapshot()}, user_request=safe_prompt,
                    resumed_from=continuation.id if continuation else None,
                )
                self.coding_store.update_run(run_id, messages=self._coding_redact(state["initial_messages"]),
                    context_report=options.get("visibility_report", []))
                self._coding_controls[session_id] = control
                self._coding_session_projects[session_id] = project_id
                control.on_event = receive
                for skill in options.get("selected_skills", [])[:30]:
                    control.emit(AgentEventType.SKILL_ACTIVATED, f"Beceri talimatları bağlama eklendi: {skill}",
                                 skill=skill, source="selected_instructions")
                if legacy.get("network_access") in denied or legacy.get("install_packages") in denied:
                    control.emit(AgentEventType.TASK_STATE,
                        "Proje izinleri ağ erişimini veya paket kurulumunu engelliyor; genel terminal ve test komutları kapatıldı.",
                        state=control.state)
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                config = deepcopy(self.config)
                config["mcp_servers"] = {}
                config["plugins"] = dict(config.get("plugins", {}), autoload_builtins=False, directory="")
                config["skills"] = dict(config.get("skills", {}), active_skills=[])
                config["computer_use"] = {"enabled": False}
                agent = Agent(config=config, config_path=str(self.config_path) if self.config_path else None)
                # Initialize providers/meta registries without plugin discovery or
                # implicit MCP connections. Only CodingToolFilter is exposed.
                agent._is_initialized = True
                agent.providers.set_active(options.get("provider", self.provider))
                agent.providers.get_active().set_model(session.model)
                self.configure_model_metadata(agent.providers.get_active())
                policy_registry = self.mcp_manager.tool_registry
                # Honor server deny/allow lists as well as exact session selection.
                from firik_agent.core.session import SessionToolFilter
                gate = SessionToolFilter(policy_registry, self.mcp_manager)
                mcp_enabled = control.permissions["allow_mcp"] and control.mode not in {"ask", "plan"}
                session.active_mcp_servers = set(options.get("mcp_servers", [])) if mcp_enabled else set()
                allowed = {name for name in options.get("mcp_tools", [])
                           if mcp_enabled and policy_registry.get_tool(name) is not None
                           and policy_registry.get_tool(name).source.startswith("mcp:")
                           and gate.is_tool_allowed(name, session)}
                selected_servers = {policy_registry.get_tool(name).source[4:] for name in allowed}
                connected = set()
                for name in selected_servers:
                    # Track attempts too: acquire may add a session association
                    # before a failed network connection needs cleanup.
                    acquired.add(name)
                    control.emit(AgentEventType.MCP_CONNECTING, f"MCP bağlantısı kuruluyor: {name}", server=name)
                    if await self.mcp_manager.acquire_for_session(session_id, name):
                        connected.add(name)
                        control.emit(AgentEventType.MCP_CONNECTED, f"MCP bağlantısı hazır: {name}", server=name)
                    else:
                        control.emit(AgentEventType.MCP_ERROR, f"MCP bağlantısı kurulamadı: {name}", server=name, status="failed")
                session.active_mcp_servers = connected
                allowed = {name for name in allowed if gate.is_tool_allowed(name, session)}
                tool_filter = CodingToolFilter(cwd, control, mcp_registry=policy_registry,
                                               trusted_mcp_tools=allowed)
                self._coding_filters[session_id] = tool_filter
                baseline = await asyncio.to_thread(self.checkpoint_store.create_checkpoint,
                    project_id, cwd, run_id=run_id, label="Çalışma öncesi", kind="before_run")
                checkpoints.append({"kind": "source", "checkpoint_id": baseline["id"], "created_at": time.time()})
                control.emit(AgentEventType.CHECKPOINT_CREATED, "Çalışma öncesi dosyalar kaydedildi.", checkpoint=baseline)
                def checkpoint_file(path):
                    point = self._checkpoint_before_edit(project_id, cwd, run_id, path)
                    control.emit(AgentEventType.CHECKPOINT_CREATED, "Dosya düzenleme kontrol noktası.", checkpoint=point)
                tool_filter.before_file_mutation = checkpoint_file
                if options.get("quality_gates") is not None:
                    tool_filter.configure_quality_gates(options["quality_gates"])
                elif options.get("auto_quality_gates"):
                    await asyncio.to_thread(tool_filter.discover_quality_gates)
                stream_filter = tool_filter
                if options.get("subagents_enabled") is True:
                    from firik_agent.projects.subagents import SubagentToolFilter
                    def child_agent():
                        worker = Agent(config=deepcopy(config), config_path=str(self.config_path) if self.config_path else None)
                        worker._is_initialized = True
                        worker.providers.set_active(options.get("provider", self.provider))
                        worker.providers.get_active().set_model(session.model)
                        self.configure_model_metadata(worker.providers.get_active())
                        return worker
                    selected_provider = options.get("provider", self.provider)
                    subagents = SubagentToolFilter(tool_filter, session, child_agent, receive, self._coding_redact,
                        provider_resolver=lambda p, m: self._coding_worker(p or selected_provider, m or session.model),
                        limits=options.get("subagent_limits"))
                    self._coding_managers[session_id] = subagents
                    stream_filter = subagents
                    session.system_prompt += (
                        "\nAraştırmacı, inceleyici, çalışan ve test uzmanı alt ajanlar kullanılabilir. Bağımsız işlerde spawn_subagent kullan. "
                        "Göreve ilgili dosyaları ve beklenen çıktıyı ekle. Düzenleyen çalışan için write_paths dosya kapsamını belirt. "
                        "İzinler ve bütçe ana çalışmadan daraltılır; sonuçları doğrula. send_message ile görev sözleşmelerini paylaş. "
                        "Son yanıtı vermeden önce açtığın tüm alt ajanları wait_subagent ile bekle veya cancel_subagent ile durdur."
                    )
                if continuation:
                    revalidation = self._coding_revalidate(tool_filter, continuation.file_journal)
                    checkpoints.append({"kind": "resume_revalidation", "previous_run_id": continuation.id,
                                        "files": revalidation, "created_at": time.time()})
                    changed = [item["path"] for item in revalidation if not item["matches"]]
                    if changed:
                        session.system_prompt += "\nÖnceki çalışmadan sonra değişen dosyaları yeniden oku; kullanıcının değişikliklerini koru: " + ", ".join(changed)
                    self.coding_store.checkpoint(run_id, checkpoints[-1])
                await control.checkpoint()
                async for event in agent.run_stream(prompt=safe_prompt, session=session,
                                                     session_tool_filter=stream_filter,
                                                     cancel_event=cancel_event, run_control=control, cwd=cwd):
                    receive(event)
                    if event.type == AgentEventType.ERROR:
                        status = "failed"
                        final = event.content or "Model isteği başarısız."
                    elif event.type == AgentEventType.DONE:
                        final = event.content or ""
                if cancel_event.is_set():
                    status = "cancelled"
                    final = "Çalışma kullanıcı tarafından durduruldu."
                    terminal_events.clear()
                elif status == "completed":
                    if options.get("auto_quality_gates") and control.mode not in {"ask", "plan"}:
                        for gate in control.quality_gates:
                            if gate["enabled"]:
                                await tool_filter.run_quality_gate(gate["id"])
                    completion = await tool_filter.completion_gate_async()
                    if not completion["passed"]:
                        status = "blocked"
                        final = (final + "\n\nTamamlanma engellendi:\n" + "\n".join(completion["reasons"])).strip()
                        terminal_events.clear()
                        control.set_state("blocked", final)
            except asyncio.CancelledError:
                status, final = "cancelled", "Çalışma kullanıcı tarafından durduruldu."
            except Exception as error:
                status, final = "failed", str(error)
            finally:
                if subagents is not None:
                    try:
                        await subagents.close()
                    except Exception as error:
                        status, final = "failed", f"Alt ajan kaydı saklanamadı: {self._coding_redact(str(error))}"
                        terminal_events.clear()
                if session is not None and initial_system_prompt is not None:
                    session.system_prompt = initial_system_prompt
                state["terminal_status"] = status
                if status == "cancelled":
                    terminal_events.clear()
                try:
                    persist_snapshot(status=status, final=final, force=True)
                except Exception as error:
                    status = "failed"
                    final = f"Çalışma kaydı saklanamadı: {self._coding_redact(str(error))}"
                    terminal_events.clear()
                try:
                    for name in acquired:
                        try:
                            await self.mcp_manager.release_for_session(session_id, name)
                        except Exception:
                            logger.warning("Kodlama MCP bağlantısı serbest bırakılamadı: %s", name)
                finally:
                    release_stream()
                    notify(status, final)
        coroutine = run()
        try:
            future = self.runtime.run_coroutine(coroutine)
        except Exception:
            coroutine.close()
            release_stream()
            raise
        with self._stream_lock:
            if self._streams.get(session_id) is cancel_event:
                self._stream_futures[session_id] = future
        def completed_before_entry(future):
            if not entered.is_set():
                release_stream()
                notify("cancelled" if future.cancelled() else "failed", "Çalışma başlamadan durduruldu.")
        future.add_done_callback(completed_before_entry)
        return future

    def _coding_redact(self, value):
        from firik_agent.projects.coding_sessions import redact_sensitive
        if isinstance(value, str):
            # Tool argument strings are JSON documents, so redact credential keys
            # before rebuilding them rather than applying only text patterns.
            if value.lstrip().startswith(("{", "[")):
                try:
                    return json.dumps(self._coding_redact(json.loads(value)), ensure_ascii=False)
                except (ValueError, TypeError):
                    pass
            return redact_secrets(redact_sensitive(value, getattr(self, "_coding_known_secrets", ())))
        if isinstance(value, dict):
            return {key: self._coding_redact(item) for key, item in redact_sensitive(value, getattr(self, "_coding_known_secrets", ())).items()}
        if isinstance(value, list):
            return [self._coding_redact(item) for item in value]
        return value

    @staticmethod
    def _coding_revalidate(tool_filter, journal):
        latest = {}
        for change in journal:
            if change.get("path"):
                latest[change["path"]] = change
        report = []
        for path, change in latest.items():
            resolved = tool_filter._resolve(path, must_exist=False)
            current = hashlib.sha256(tool_filter._read_bytes(path)).hexdigest() if resolved.exists() else None
            expected = change.get("before_hash") if change.get("undone") else change.get("after_hash")
            report.append({"path": path, "expected_hash": expected, "current_hash": current, "matches": expected == current})
        return report

    def _coding_skill_definitions(self, project_id, session_id):
        """Read bounded metadata; selected instruction bodies are loaded lazily."""
        import yaml
        from firik_agent.skills.skill import Skill
        project = self.projects.db.get_project(project_id)
        if project is None:
            raise ValueError("Proje bulunamadı.")
        manager = self.get_agent(session_id).skills
        roots = [manager.skills_dir, Path(__file__).parent.parent / "builtin_skills",
                 Path(project.local_path) / "skills"]
        definitions, seen = [], set()
        for root in roots:
            if root.is_symlink() or not root.is_dir():
                continue
            for folder in sorted(root.iterdir())[:200]:
                if folder.is_symlink() or not folder.is_dir():
                    continue
                md, config = folder / "SKILL.md", folder / "skill.yaml"
                if (md.is_symlink() or config.is_symlink()) or not (md.is_file() or config.is_file()):
                    continue
                metadata = {}
                if config.is_file() and config.stat().st_size <= 16 * 1024:
                    try:
                        loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
                    except (yaml.YAMLError, OSError, UnicodeError):
                        continue
                    metadata = dict(loaded) if isinstance(loaded, dict) else {}
                if md.is_file():
                    with md.open(encoding="utf-8") as stream:
                        first = stream.readline(1024)
                        if first.strip() == "---":
                            frontmatter, size = [], 0
                            while size <= 16 * 1024:
                                line = stream.readline(16 * 1024 - size + 1)
                                if not line:
                                    break
                                size += len(line)
                                if size > 16 * 1024 or line.strip() == "---":
                                    break
                                frontmatter.append(line)
                            try:
                                loaded = yaml.safe_load("".join(frontmatter))
                            except yaml.YAMLError:
                                continue
                            if isinstance(loaded, dict):
                                metadata.update(loaded)
                        else:
                            metadata.setdefault("description", first.strip()[:300])
                name = str(metadata.get("name") or folder.name)
                if name in seen:
                    continue
                seen.add(name)

                def load_instructions(path=folder):
                    source = path / "SKILL.md"
                    if source.is_symlink() or path.is_symlink() or (source.is_file() and source.stat().st_size > 1024 * 1024):
                        raise ValueError("Beceri talimat dosyası güvenle okunamıyor.")
                    return self._coding_redact(Skill.load_from_directory(path).instructions)

                definitions.append({"name": name, "description": str(metadata.get("description") or name),
                                    "tags": list(metadata.get("tags") or []), "trigger": str(metadata.get("trigger") or ""),
                                    "resources": list(metadata.get("resources") or []), "instructions": load_instructions})
        return definitions
