"""Desktop adapters for the existing coding, Git and native voice services.

Slow context/Git/device work runs outside the stdio reader, so approvals,
cancellation and reverse browser RPC remain available during an operation.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import threading
import time
from uuid import uuid4

from firik_agent.core.session import ChatSession
from firik_agent.desktop_rpc.models import (
    Ack, ChatMessage, ChatStartParams, CodingApproval, CodingHistoryResult,
    CodingPreferences, CodingRecordInfo, CodingState, GitFileInfo, GitOverview, GitPreview, GitResult, GitTool,
    METHODS, ProjectFileEntry, ProjectFilesResult, ProjectInfo, ProjectReadResult, ProjectsResult, VoicePreferences, VoiceState,
)
from firik_agent.projects.git_service import GitService
from firik_agent.projects.explorer import ProjectExplorer
from firik_agent.projects.indexer import IGNORED_EXTENSIONS, read_project_text, safe_project_path
from firik_agent.desktop_rpc.terminal import TerminalRPC


WORKSPACE_METHODS = frozenset(name for name in METHODS if name.startswith(
    ("projects.", "coding.", "git.", "voice.", "terminal.")))


class WorkspaceRPC:
    def __init__(self, server):
        self.server, self.service = server, server.service
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="FirikWorkspaceRPC")
        self._controls = ThreadPoolExecutor(max_workers=1, thread_name_prefix="FirikWorkspaceControl")
        self._slots = threading.BoundedSemaphore(16)
        self._control_slots = threading.BoundedSemaphore(8)
        self._lock = threading.RLock()
        self._states: dict[str, CodingState] = {}
        self._cancelled: set[str] = set()
        self._project_locks: dict[str, threading.RLock] = {}
        self._previews: dict[str, dict] = {}
        self._voice = None
        self._voice_scope = ""
        self._voice_transcript = ""
        self._voice_review = ""
        self._voice_error = None
        self._voice_router = None
        self._voice_response_sid = None
        self._voice_operation = threading.RLock()
        self._closed = False
        self.terminal = TerminalRPC(self)

    def fail(self, message, code=-32009):
        from firik_agent.desktop_rpc.server import RPCError
        raise RPCError(code, message)

    def handle(self, request_id, method, params, result_type):
        if not self.server._hello:
            self.fail("Önce protokol sürümü doğrulanmalıdır.", -32000)
        control = method in {"coding.control", "voice.command", "terminal.cancel"}
        slots = self._control_slots if control else self._slots
        if self._closed or not slots.acquire(blocking=False):
            self.fail("Çalışma alanı meşgul; kısa süre sonra yeniden deneyin.")

        def work():
            try:
                if self._closed:
                    self.fail("Çalışma alanı kapatılıyor.")
                result = self.dispatch(method, params)
                checked = result_type.model_validate(result)
                self.server.send({"jsonrpc": "2.0", "id": request_id, "result": checked.model_dump()})
            except Exception as error:
                from firik_agent.desktop_rpc.server import RPCError
                known = isinstance(error, (RPCError, ValueError, PermissionError))
                message = self.server._clean(str(error), 1024) if known else "Çalışma alanı işlemi tamamlanamadı."
                self.server.send({"jsonrpc": "2.0", "id": request_id, "error": {
                    "code": error.code if isinstance(error, RPCError) else -32001, "message": message}})
            finally:
                slots.release()

        (self._controls if control else self._pool).submit(work)

    def _project(self, project_id):
        project = self.service.projects.db.get_project(project_id)
        if project is None:
            self.fail("Proje bulunamadı.", -32004)
        if not Path(project.local_path).is_dir():
            self.fail("Proje klasörü bulunamadı.", -32004)
        return project

    def _project_lock(self, project_id):
        with self._lock:
            return self._project_locks.setdefault(project_id, threading.RLock())

    def coding_busy(self):
        with self._lock:
            return any(state.busy for state in self._states.values()) or self.terminal.busy()

    @staticmethod
    def _project_info(project):
        return ProjectInfo(project_id=project.id, name=project.name, local_path=project.local_path)

    def _preferences(self, project_id):
        """Report effective capabilities without lifting inherited project denials."""
        settings = self.service.projects.db.get_settings(project_id)
        prefs = settings.coding_preferences
        permissions = {key: prefs.get("permissions", {}).get(key, True) is True
                       for key in CodingPreferences.model_fields}
        for legacy, names in {
            "read_files": ("allow_reads",), "write_files": ("allow_edits",),
            "terminal": ("allow_terminal", "allow_tests"),
            "network_access": ("allow_terminal", "allow_tests"),
            "install_packages": ("allow_terminal", "allow_tests"),
        }.items():
            if settings.permissions.get(legacy) in {"never", "deny"}:
                permissions.update({name: False for name in names})
        policy = prefs.get("policy", {})
        for action, name in {"filesystem.read": "allow_reads", "filesystem.write": "allow_edits"}.items():
            if policy.get(action) == "deny":
                permissions[name] = False
        if prefs.get("excluded_files") or all(policy.get(action) == "deny" for action in ("shell.safe", "shell.network")):
            permissions.update(allow_terminal=False, allow_tests=False)
        return CodingPreferences(**permissions)

    def _explorer(self, project_id):
        project = self._project(project_id)
        if not self._preferences(project_id).allow_reads:
            self.fail("Bu projede dosya okuma izni kapalı.", -32003)
        prefs = self.service.projects.db.get_settings(project_id).coding_preferences
        return ProjectExplorer(project.local_path, ignored_dirs=prefs.get("ignored_dirs"),
                               show_hidden=bool(prefs.get("show_hidden", False)))

    def _exclusion_filter(self, project_id):
        project = self._project(project_id)
        excluded = self.service.projects.db.get_settings(project_id).coding_preferences.get("excluded_files", [])
        paths = []
        for value in excluded:
            try:
                value = safe_project_path(project.local_path, value).relative_to(Path(project.local_path).resolve()).as_posix()
            except (ValueError, OSError):
                value = str(value).replace("\\", "/").rstrip("/")
            paths.append(value)
        return lambda relative: any(value == "." or relative == value or relative.startswith(value + "/") for value in paths)

    def _context_paths(self, project_id, paths):
        """Validate explicit references before recording or starting a run."""
        if not paths:
            return []
        explorer = self._explorer(project_id)
        excluded = self._exclusion_filter(project_id)
        result = []
        for value in paths:
            path = safe_project_path(explorer.root, value)
            relative = path.relative_to(explorer.root).as_posix()
            if (not path.exists() or not explorer._visible(relative, path.is_dir())
                    or excluded(relative)):
                self.fail("Seçili bağlam yolu bulunamadı veya proje kapsamı dışında.", -32602)
            if not path.is_dir():
                read_project_text(explorer.root, relative, max_bytes=0)
            if relative not in result:
                result.append(relative)
        return result

    def _state(self, project_id, session_id):
        self._project(project_id)
        with self._lock:
            state = self._states.get(session_id)
            if state is None or state.project_id != project_id:
                self.fail("Kodlama oturumu bu projeye ait değil.", -32004)
            return state

    def _messages(self, session_id, rows):
        visible = [row for row in rows if row.get("role") in {"user", "assistant"}]
        messages = [ChatMessage(id=f"{session_id}:{i}", role=row["role"],
                            content=self.server._clean(self.server._display_content(row.get("content", "")), 65536),
                            pending=False, timestamp="") for i, row in enumerate(visible[-100:])]
        return self._limit_messages(messages)

    @staticmethod
    def _limit_messages(messages):
        kept, remaining = [], 200000
        for row in reversed(messages[-100:]):
            if remaining <= 0:
                break
            row.content = row.content[-remaining:]
            remaining -= len(row.content)
            kept.append(row)
        return list(reversed(kept))

    def coding_state(self, project_id, session_id):
        state = self._state(project_id, session_id)
        with self._lock:
            result = state.model_copy(deep=True)
        control = self.service._coding_controls.get(session_id)
        if result.busy and control is not None:
            snapshot = control.snapshot()
            result.status = str(snapshot["state"])
            result.plan = self.server._clean(snapshot.get("plan", []), 32768)
            result.changes = self.server._clean(snapshot.get("changes", []), 32768)
            result.tests = self.server._clean(snapshot.get("tests", []), 32768)
            result.approvals = [CodingApproval(request_id=key,
                tool_name=str(value.get("tool_name", "İşlem")), preview=self.server._clean(value, 8192))
                for key, value in snapshot.get("pending_approvals", {}).items()]
        return result

    def _coding_event(self, session_id, run_id, event):
        with self._lock:
            state = self._states[session_id]
            if state.run_id != run_id or not state.busy or self._closed:
                return
            kind = event.type.value
            state.record_id = str(event.metadata.get("run_id") or state.record_id)
            if kind == "model_request_started":
                # A tool round may produce several assistant messages.
                if state.messages and state.messages[-1].role == "assistant":
                    state.messages[-1].pending = False
                state.messages.append(ChatMessage(id=uuid4().hex, role="assistant", content="", pending=True, timestamp=""))
            elif kind == "text_delta":
                if not state.messages or state.messages[-1].role != "assistant":
                    state.messages.append(ChatMessage(id=uuid4().hex, role="assistant", content="", pending=True, timestamp=""))
                state.messages[-1].content = (state.messages[-1].content + (event.content or ""))[-65536:]
            elif kind == "task_state":
                state.status = str(event.metadata.get("state", state.status))
            state.messages = self._limit_messages(state.messages)
            project_id = state.project_id
        self.terminal.coding_event(project_id, session_id, run_id, event)
        self.server.emit("coding", project_id=project_id, conversation_id=session_id, run_id=run_id,
                         event_type=kind)

    def _finish_coding(self, session_id, run_id, text="", error=None):
        record = self.service.coding_store.latest_run(self._states[session_id].project_id, session_id=session_id)
        with self._lock:
            state = self._states[session_id]
            if state.run_id != run_id or not state.busy:
                return
            cancelled = run_id in self._cancelled
            self._cancelled.discard(run_id)
            state.busy = False
            state.status = "cancelled" if cancelled else "failed" if error else record.status if record else "completed"
            state.error = self.server._clean(error, 8192) if error else None
            state.approvals = []
            if record and record.id == state.record_id:
                state.record_id = record.id
                state.messages = self._messages(session_id, record.messages)
                state.plan = record.plan[:32768]
                state.changes = self.server._clean(record.file_journal, 32768)
                state.tests = self.server._clean(record.tests_executed, 32768)
            if text and not any(row.content == text for row in state.messages[-2:]):
                state.messages.append(ChatMessage(id=uuid4().hex, role="assistant",
                    content=self.server._clean(text, 65536), pending=False, timestamp=""))
            for message in state.messages:
                message.pending = False
            project_id = state.project_id
        self.server.emit("coding", project_id=project_id, conversation_id=session_id, run_id=run_id, event_type="finished")

    def _start_coding(self, p):
        project = self._project(p.project_id)
        with self._project_lock(p.project_id):
            state = self._state(p.project_id, p.session_id)
            prefs = self.service.projects.db.get_settings(p.project_id).coding_preferences
            context_paths = (self._context_paths(p.project_id, p.context_paths) if p.context_paths is not None
                             else list(prefs.get("context_paths", [])))
            with self._lock:
                if self.terminal.busy(p.project_id):
                    self.fail("Bu projede terminal komutu çalışıyor; bitmesini bekleyin veya durdurun.")
                if any(s.busy and s.project_id == p.project_id for s in self._states.values()):
                    self.fail("Bu projede bir kodlama çalışması zaten yürütülüyor.")
                if not self.server.settings().has_api_key:
                    self.fail("Önce Ayarlar bölümünden API anahtarınızı kaydedin.", -32010)
                if p.reasoning_effort is not None and p.reasoning_effort not in self.service.model_effort_levels(p.model):
                    self.fail("Bu model için doğrulanmış bir reasoning seviyesi seçin.", -32602)
                run_id = uuid4().hex
                state.run_id, state.busy, state.status = run_id, True, "preparing"
                state.record_id = ""
                state.error, state.model, state.mode = None, p.model, p.mode
                state.reasoning_effort = p.reasoning_effort
                state.plan = state.changes = state.tests = ""
                state.approvals = []
                state.messages.append(ChatMessage(id=uuid4().hex, role="user", content=p.prompt, pending=False, timestamp=""))
            session = self.service.sessions[p.session_id]
            session.model, session.reasoning_effort = p.model, p.reasoning_effort
            permissions = {**prefs.get("permissions", {}),
                           **self._preferences(p.project_id).model_dump(),
                           "allow_mcp": False, "allow_browser": False, "allow_subagents": False}
            try:
                prepared = self.service.prepare_coding_async(p.project_id, p.prompt,
                    on_success=lambda _: None, on_error=lambda _: None,
                    session_id=p.session_id, model=p.model, permissions=permissions,
                    relevant_paths=context_paths).result(timeout=120)
                with self._lock:
                    cancelled = run_id in self._cancelled or self._closed
                if cancelled:
                    self._finish_coding(p.session_id, run_id)
                    return self.coding_state(p.project_id, p.session_id)
                session.system_prompt = prepared.get("trusted_system_prompt", prepared["system_prompt"])
                options = {"project_id": p.project_id, "mode": p.mode, "permissions": permissions,
                    "provider": self.service.provider, "subagents_enabled": False,
                    "policy": dict(prefs.get("policy", {})),
                    "dirty_start": prefs.get("dirty_start", "reject"),
                    "execution_config": dict(prefs.get("execution_config", {})),
                    "excluded_paths": list(prefs.get("excluded_files", [])),
                    "quality_gates": prefs.get("quality_gates"),
                    "auto_quality_gates": bool(prefs.get("auto_quality_gates", False))}
                for key in ("project_context", "harness_profile", "context_pipeline", "context_items",
                            "visibility_report", "capabilities", "selected_skills"):
                    if key in prepared:
                        options[key] = prepared[key]
                options["excluded_sources"] = prepared.get("unavailable_report", [])
                self.service.chat_agent_stream_async(p.session_id, prepared.get("user_prompt", p.prompt),
                    cwd=project.local_path, coding_options=options,
                    on_event=lambda evt: self._coding_event(p.session_id, run_id, evt),
                    on_done=lambda text: self._finish_coding(p.session_id, run_id, text),
                    on_error=lambda error: self._finish_coding(p.session_id, run_id, error=error))

                # The shared bridge calls on_done/on_error after durable cleanup,
                # including cancellation before coroutine entry. Future.cancel()
                # alone does not mean its shielded finalization has finished.
            except Exception:
                self._finish_coding(p.session_id, run_id, error="Kodlama çalışması başlatılamadı.")
                raise
        return self.coding_state(p.project_id, p.session_id)

    def voice_preferences(self):
        settings = self.service.voice_settings
        return VoicePreferences(**{key: getattr(settings, key) for key in VoicePreferences.model_fields})

    def voice_state(self):
        controller = self._voice
        return VoiceState(conversation_id=controller.session_id, scope_id=self._voice_scope,
            state=controller.state_id.value, conversation_active=controller.conversation_active,
            paused=controller.paused, transcript=self._voice_transcript, review_id=self._voice_review,
            error=self._voice_error, settings=self.voice_preferences())

    def _emit_voice(self):
        if self._voice is not None and not self._closed:
            sid = self._voice.session_id
            # Controller callbacks can hold its device lock. Never acquire the
            # transport lock there: a chat completion may be using the controller.
            try:
                self._pool.submit(lambda: self.server.emit("voice", conversation_id=sid) if not self._closed else None)
            except RuntimeError:
                pass

    def _bind_voice(self, sid):
        with self.server._lock:
            self.server._session(sid)
        if self._voice is not None and self._voice.session_id == sid:
            return
        if self._voice is not None:
            self._voice.close()
        if self._voice_router is not None:
            self._voice_router.close()
        scope_id = self._voice_scope = uuid4().hex
        self._voice_response_sid = None
        self._voice_transcript, self._voice_review, self._voice_error = "", "", None

        def transcript(text):
            if self._voice_scope == scope_id and not self._closed:
                self._voice_transcript, self._voice_review = text, uuid4().hex
                self._emit_voice()

        def error(text):
            if self._voice_scope == scope_id:
                self._voice_error = self.server._clean(text, 2048)
                self._emit_voice()

        self._voice = self.service.create_voice_controller(session_id=sid,
            on_state=lambda _: self._emit_voice() if self._voice_scope == scope_id else None,
            on_transcript=transcript, on_error=error)
        from firik_agent.voice.requests import VoiceScope
        def submit(request):
            self._voice_response_sid = sid
            try:
                self.server.start(ChatStartParams(conversation_id=sid,
                    prompt=request.request_text, model=self.service.sessions[sid].model,
                    reasoning_effort=self.service.sessions[sid].reasoning_effort), voice_request=request)
            except Exception:
                self._voice_response_sid = None
                raise
            return True

        self._voice_router = self.service.create_voice_request_router(
            on_submit=submit,
            scope_provider=lambda: VoiceScope(sid, revision=scope_id))

    def voice_response(self, sid, action, text=""):
        controller = self._voice
        if (controller is None or controller.session_id != sid or self._closed
                or self._voice_response_sid != sid):
            return
        if action == "started":
            controller.response_started(from_voice=True)
        elif action == "delta":
            controller.response_delta(text)
        elif action == "completed":
            self._voice_response_sid = None
            controller.response_completed(text)

    def dispatch(self, method, p):
        if method == "terminal.list":
            return self.terminal.list(p.project_id)
        if method == "terminal.start":
            return self.terminal.start(p)
        if method == "terminal.cancel":
            return self.terminal.cancel(p)
        if method == "projects.list":
            return ProjectsResult(projects=[self._project_info(row) for row in self.service.projects.list_projects()])
        if method == "projects.create":
            root = Path(p.local_path).expanduser().resolve()
            if not root.is_dir() or not p.name.strip():
                self.fail("Mevcut bir proje klasörü ve proje adı girin.", -32602)
            for project in self.service.projects.list_projects():
                if Path(project.local_path).resolve() == root:
                    return self._project_info(project)
            return self._project_info(self.service.projects.create_project(p.name.strip(), str(root)))
        if method == "projects.files":
            explorer = self._explorer(p.project_id)
            excluded = self._exclusion_filter(p.project_id)
            directory = safe_project_path(explorer.root, p.path)
            relative = directory.relative_to(explorer.root).as_posix()
            if not directory.is_dir() or excluded(relative):
                self.fail("Proje klasörü bulunamadı veya kapsam dışında.", -32602)
            if p.query.strip():
                rows = explorer.search(p.query, limit=100)
            else:
                rows = explorer.children(p.path, limit=300)
            return ProjectFilesResult(path="" if relative == "." else relative,
                entries=[ProjectFileEntry(path=row["path"], name=row["name"], is_dir=row.get("is_dir", False))
                         for row in rows if not excluded(row["path"])
                         and (row.get("is_dir", False) or Path(row["path"]).suffix.lower() not in IGNORED_EXTENSIONS)],
                limited=explorer.limited)
        if method == "projects.read":
            explorer = self._explorer(p.project_id)
            path = safe_project_path(explorer.root, p.path)
            relative = path.relative_to(explorer.root).as_posix()
            if not explorer._visible(relative, False) or self._exclusion_filter(p.project_id)(relative):
                self.fail("Dosya proje kapsamı dışında.", -32602)
            content = read_project_text(explorer.root, relative, max_bytes=65536)
            return ProjectReadResult(path=relative, content=self.server._clean(content, 65536), truncated=path.stat().st_size > 65536)
        if method == "coding.preferences":
            self._project(p.project_id)
            return self._preferences(p.project_id)
        if method == "coding.preferences.update":
            self._project(p.project_id)
            with self._lock:
                if self.terminal.busy(p.project_id) or any(state.busy and state.project_id == p.project_id for state in self._states.values()):
                    self.fail("İzinleri değiştirmeden önce bu projedeki çalışmanın bitmesini bekleyin.")
            with self._project_lock(p.project_id):
                with self._lock:
                    if self.terminal.busy(p.project_id) or any(state.busy and state.project_id == p.project_id for state in self._states.values()):
                        self.fail("İzinleri değiştirmeden önce bu projedeki çalışmanın bitmesini bekleyin.")
                settings = self.service.projects.db.get_settings(p.project_id)
                prefs = deepcopy(settings.coding_preferences)
                prefs["permissions"] = {**prefs.get("permissions", {}),
                    **{key: getattr(p, key) for key in CodingPreferences.model_fields}}
                settings.coding_preferences = prefs
                self.service.projects.db.save_settings(settings)
            return self._preferences(p.project_id)
        if method == "coding.history":
            self._project(p.project_id)
            return CodingHistoryResult(records=[CodingRecordInfo(record_id=r.id, session_id=r.session_id,
                title=r.title, status=r.status, created_at=r.created_at)
                for r in self.service.coding_store.list_runs(p.project_id, limit=50)])
        if method == "coding.open":
            self._project(p.project_id)
            with self._project_lock(p.project_id):
                if not p.new_session and p.record_id is None:
                    with self._lock:
                        existing = next((s for s in self._states.values() if s.project_id == p.project_id), None)
                    if existing is not None:
                        return self.coding_state(p.project_id, existing.session_id)
                if p.new_session:
                    session, record = ChatSession(model=self.service.default_model, active_mcp_servers=set()), None
                    self.service.sessions[session.session_id] = session
                    self.service._coding_session_projects[session.session_id] = p.project_id
                else:
                    if p.record_id:
                        record = self.service.coding_store.get_run(p.record_id, project_id=p.project_id)
                        existing = self._states.get(record.session_id) if record is not None else None
                        if existing is not None and existing.busy:
                            return self.coding_state(p.project_id, existing.session_id)
                    session, record = self.service.restore_coding(p.project_id, p.record_id)
                with self._lock:
                    if session.session_id not in self._states or p.record_id is not None:
                        self._states[session.session_id] = CodingState(project_id=p.project_id,
                            session_id=session.session_id, model=session.model,
                            reasoning_effort=session.reasoning_effort,
                            run_id=record.id if record else "", mode=record.mode if record else "approved",
                            record_id=record.id if record else "", status=record.status if record else "idle",
                            messages=self._messages(session.session_id, record.messages) if record else [],
                            plan=record.plan[:32768] if record else "",
                            changes=self.server._clean(record.file_journal, 32768) if record else "",
                            tests=self.server._clean(record.tests_executed, 32768) if record else "",
                            error=record.error or None if record else None)
                return self.coding_state(p.project_id, session.session_id)
        if method == "coding.get":
            return self.coding_state(p.project_id, p.session_id)
        if method == "coding.start":
            return self._start_coding(p)
        if method == "coding.control":
            state = self._state(p.project_id, p.session_id)
            with self._lock:
                if state.run_id != p.run_id or not state.busy:
                    self.fail("Bu kodlama çalışması artık etkin değil.", -32004)
                if p.action == "cancel":
                    self._cancelled.add(p.run_id)
            handled = self.service.coding_control(p.session_id, p.action, p.request_id, p.allowed)
            if p.action == "cancel":
                self.service.cancel_active_stream(p.session_id)
            elif not handled:
                self.fail("İşlem artık beklemiyor veya ajan henüz hazırlanıyor.")
            return Ack()
        if method == "git.overview":
            root = self._project(p.project_id).local_path
            if not GitService.is_git_repo(root):
                self.fail("Seçili klasör bir Git deposu değil.", -32602)
            tools = [GitTool(**{key: row[key] for key in GitTool.model_fields}) for row in GitService.get_tool_catalog()]
            status = GitService.execute_tool(root, "status")
            excluded = self._exclusion_filter(p.project_id)
            files = []
            for row in GitService.get_status(root)["files"][:500]:
                try:
                    paths = [row["path"]] + ([row["original_path"]] if row["original_path"] else [])
                    for value in paths:
                        if len(value) > 4096 or any(ord(char) < 32 for char in value):
                            raise ValueError("Unsupported control character in Git path")
                        relative = safe_project_path(root, value).relative_to(Path(root).resolve()).as_posix()
                        if excluded(relative):
                            raise ValueError("Excluded Git path")
                    files.append(GitFileInfo(**row))
                except (ValueError, OSError):
                    continue
            return GitOverview(branch=GitService.get_current_branch(root),
                status=self.server._clean(status["stdout"] or status["stderr"], 32768),
                diff=self.server._clean(GitService.get_diff(root), 65536),
                commits=self.server._clean("\n".join(f"{row['short_hash']}  {row['message']} · {row['author']}"
                    for row in GitService.get_recent_commits(root, 20)), 16384), tools=tools, files=files)
        if method == "git.preview":
            root = self._project(p.project_id).local_path
            with self._project_lock(p.project_id):
                plan = GitService.preview_tool(root, p.tool_id, p.arguments)
                if not plan["ok"]:
                    self.fail(plan["error"], -32602)
                token = uuid4().hex
                with self._lock:
                    self._previews = {key: value for key, value in self._previews.items() if value["expires"] > time.monotonic()}
                    if len(self._previews) >= 64:
                        self._previews.pop(next(iter(self._previews)))
                    self._previews[token] = dict(project_id=p.project_id, root=str(Path(root).resolve()),
                        tool_id=p.tool_id, arguments=deepcopy(p.arguments), plan=plan, expires=time.monotonic() + 300)
                return GitPreview(token=token, title=plan["title"], command=self.server._clean(plan["command"], 16384),
                    requires_confirmation=plan["requires_confirmation"], warnings=plan["warnings"])
        if method == "git.execute":
            root = self._project(p.project_id).local_path
            with self._project_lock(p.project_id):
                with self._lock:
                    preview = self._previews.get(p.token)
                    if (not preview or preview["project_id"] != p.project_id
                            or preview["root"] != str(Path(root).resolve()) or preview["expires"] <= time.monotonic()):
                        self.fail("Git önizlemesi geçersiz veya süresi dolmuş; yeniden önizleyin.", -32004)
                    if preview["plan"]["mutates"] and not p.confirmed:
                        self.fail("Git komutunu inceleyip ekrandan onaylayın.")
                    if preview["plan"]["mutates"] and (self.terminal.busy(p.project_id) or any(s.busy and s.project_id == p.project_id for s in self._states.values())):
                        self.fail("Önce bu projede çalışan kodlama ajanını veya terminal komutunu durdurun.")
                    self._previews.pop(p.token)
                result = GitService.execute_tool(root, preview["tool_id"], preview["arguments"],
                    confirmed=p.confirmed, expected_command=preview["plan"]["command"],
                    expected_targets=preview["plan"]["targets"])
                result = {key: result[key] for key in GitResult.model_fields}
                for key in ("stdout", "stderr", "command"):
                    result[key] = self.server._clean(result[key], 192 * 1024)
                return GitResult(**result)
        if method == "voice.settings":
            with self._voice_operation:
                self.service.update_voice_settings(replace(self.service.voice_settings, **p.model_dump()))
                self._voice_transcript, self._voice_review = "", ""
                self._emit_voice()
                return self.voice_preferences()
        if method in {"voice.get", "voice.command"}:
            with self._voice_operation:
                if method == "voice.get":
                    self._bind_voice(p.conversation_id)
                    return self.voice_state()
                if self._voice is None or self._voice.session_id != p.conversation_id or self._voice_scope != p.scope_id:
                    self.fail("Ses oturumu değişti; güncel sohbeti yeniden açın.", -32004)
                c = self._voice
                self._voice_error = None
                if p.action in {"submit", "discard"}:
                    if not self._voice_review or p.review_id != self._voice_review:
                        self.fail("Bu transkript artık güncel değil.", -32004)
                    if p.action == "submit":
                        from firik_agent.voice.requests import VoiceScope
                        if not p.text.strip():
                            self.fail("Gönderilecek metni girin.", -32602)
                        request = self._voice_router.create_request(self._voice_transcript,
                            VoiceScope(p.conversation_id, revision=p.scope_id), request_text=p.text)
                        request = self._voice_router.confirm(request)
                        c.response_started(from_voice=True)
                        c.transcript_resolved(submitted=True)
                        try:
                            self._voice_router.route(request)
                        except Exception:
                            c.response_completed()
                            c.review_text(self._voice_transcript)
                            raise
                    else:
                        c.transcript_resolved(submitted=False)
                    self._voice_transcript, self._voice_review = "", ""
                else:
                    actions = {"record": c.start_recording, "stop_recording": c.stop_recording,
                        "conversation": c.start_conversation, "pause": c.pause_conversation,
                        "resume": c.resume_conversation, "cancel": c.cancel,
                        "speak": lambda: c.speak(p.text[:8000]), "stop_speaking": c.stop_speaking}
                    if actions[p.action]() is False:
                        self.fail("Ses işlemi başlatılamadı; ses ayarlarını ve mevcut durumu kontrol edin.")
                    if p.action == "cancel":
                        self._voice_response_sid = None
                        self._voice_transcript, self._voice_review = "", ""
                return self.voice_state()
        self.fail("Yöntem bulunamadı.", -32601)

    def close(self):
        self._closed = True
        self.terminal.close()
        with self._lock:
            active = [state for state in self._states.values() if state.busy]
            self._cancelled.update(state.run_id for state in active)
            self._previews.clear()
        for state in active:
            self.service.coding_control(state.session_id, "cancel")
            self.service.cancel_active_stream(state.session_id)
        if self._voice is not None:
            self._voice.close()
        if self._voice_router is not None:
            self._voice_router.close()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._controls.shutdown(wait=True, cancel_futures=True)
