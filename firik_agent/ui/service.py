"""firik masaüstü uygulaması arka plan API servis köprüsü.

Tüm ağ isteklerini arka plan iş parçacıklarında (threads) çalıştırarak
grafik arayüzün (GUI) donmasını engeller ve tüm hata mesajlarını
anlaşılır Türkçe metinlere dönüştürür.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from concurrent.futures import Future
import json
import logging
import os
import threading
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx

from firik_agent.api.client import DEFAULT_BASE_URL, FirikAPI, FirikAPIError, media_data_url
from firik_agent.config import (
    add_mcp_server_to_config,
    get_mcp_servers_from_config,
    load_config,
    remove_mcp_server_from_config,
    save_config,
)
from firik_agent.core.agent import Agent
from firik_agent.core.chat_history import ChatHistoryStore
from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.core.runtime import AsyncRuntime
from firik_agent.core.session import ChatSession, SessionToolFilter
from firik_agent.credentials import get_api_key, set_api_key
from firik_agent.mcp.connection import MCPConnectionManager
from firik_agent.mcp.defaults import add_desktop_defaults
from firik_agent.mcp.models import (
    MCPLogEntry,
    MCPServerConfig,
    MCPStatus,
    MCPTestResult,
    store_mcp_secret,
)
from firik_agent.projects.service import ProjectService
from firik_agent.projects.workspace import WorkspaceSnapshot, capture_workspace, compare_workspaces
from firik_agent.ui.coding_bridge import CodingBridge

logger = logging.getLogger(__name__)



# Çevrimdışı veya ilk yüklemede gösterilecek popüler varsayılan modeller
VARSAYILAN_MODELLER = [
    "glm-5.3",
    "qwen-2.5-72b-instruct",
    "deepseek-r1",
    "dots-ocr",
    "qwen3-asr-1.7b",
    "qwen3-reranker-8b",
]


def turkce_hata_mesaji(hata: Exception) -> str:
    """Teknik istisnaları kullanıcı dostu Türkçe hata mesajlarına çevirir."""
    if isinstance(hata, FirikAPIError):
        durum = getattr(hata, "status_code", 0)
        govde = getattr(hata, "body", "")
        detay = ""
        if isinstance(govde, dict):
            detay = govde.get("detail") or govde.get("message") or json.dumps(govde, ensure_ascii=False)
        elif govde:
            detay = str(govde)

        if durum == 401:
            return "Kimlik doğrulama başarısız (401): API anahtarınız geçersiz veya eksik. Lütfen Ayarlar sekmesinden geçerli bir LLM Çıkarım anahtarı girin."
        elif durum == 402:
            return f"Yetersiz Kredi (402): Hesabınızdaki kredi tükenmiş. {detay}"
        elif durum == 403:
            return f"Erişim Engellendi (403): Anahtar yetkisi yetersiz veya Kullanım Şartları kabul edilmemiş. Lütfen 'Kota ve Durum' sekmesinden şartları onaylayın. {detay}"
        elif durum == 429:
            return f"Hız/Kota Sınırı Aşıldı (429): Lütfen bir süre bekleyip tekrar deneyin. {detay}"
        elif durum == 503:
            return "Sunucu Hizmet Veremiyor (503): FIRIK sunucuları şu anda meşgul veya bakımda."
        return f"FIRIK API Hatası ({durum}): {detay or str(hata)}"

    if isinstance(hata, httpx.ConnectError):
        return "Sunucuya bağlanılamadı. Lütfen internet bağlantınızı veya API adresini (URL) kontrol edin."
    if isinstance(hata, httpx.TimeoutException):
        return "İstek zaman aşımına uğradı. Sunucu yanıt vermedi, lütfen zaman aşımı süresini artırın veya tekrar deneyin."
    if isinstance(hata, ValueError):
        return str(hata)
    return f"Beklenmeyen bir hata oluştu: {str(hata)}"


class FirikService(CodingBridge):
    """firik masaüstü uygulaması için merkezi API yöneticisi."""

    def __init__(self, dispatch=None) -> None:
        self._dispatch = dispatch or (lambda callback, *args: callback(*args))
        self.config_path = None
        is_root_cwd = False
        try:
            is_root_cwd = Path.cwd().parent == Path.cwd()
        except Exception:
            pass
        if getattr(sys, "frozen", False) or is_root_cwd:
            if sys.platform == "win32":
                base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
            elif sys.platform == "darwin":
                base = Path.home() / "Library/Application Support"
            else:
                base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            self.config_path = base / "firik" / "config.yaml"
        self.config = load_config(self.config_path)
        add_desktop_defaults(self.config)
        self.provider = "firik"
        self._provider_cfg = self.config.get("providers", {}).get(self.provider, {})
        self.base_url = self._provider_cfg.get("base_url", DEFAULT_BASE_URL)
        self.default_model = self._provider_cfg.get("default_model", "glm-5.3")
        self.timeout = float(self.config.get("timeout", 180.0))
        self._model_metadata: Dict[str, Dict[str, Any]] = {}
        self._model_metadata_url = self.base_url
        self._cached_models: List[str] = VARSAYILAN_MODELLER.copy()
        self._active_stream_cancel: Optional[threading.Event] = None
        self._stream_lock = threading.RLock()
        self._streams: Dict[str, threading.Event] = {}
        self._stream_futures: Dict[str, Future] = {}
        self._session_agents: Dict[str, Agent] = {}
        self._computer_use_stopped = False
        db_path = None
        if self.config_path:
            db_path = self.config_path.parent / "projects.db"
        self.projects = ProjectService(db_path=db_path)
        history_path = (self.config_path.parent if self.config_path else Path.home() / ".firik") / "chats.db"
        self.chat_history = ChatHistoryStore(history_path)

        # Core runtime, MCP pool & session managers
        self.runtime = AsyncRuntime.get_instance()
        self.mcp_manager = MCPConnectionManager()
        self.mcp_manager.load_from_config(self.config.get("mcp_servers", {}))
        self.session_tool_filter = SessionToolFilter(self.mcp_manager.tool_registry, self.mcp_manager)
        self.sessions: Dict[str, ChatSession] = {}
        self._init_coding_bridge()
        self.agent: Optional[Agent] = None
        self._mcp_listeners: List[Callable[[], None]] = []
        self.mcp_manager.add_listener(self._on_mcp_status)

        # Connect autostart servers in background
        self.runtime.submit(self.mcp_manager.autostart_servers())


    def _on_mcp_status(self, name, status) -> None:
        if status == MCPStatus.CONNECTED and self._computer_use_stopped:
            connection = self.mcp_manager.get_connection(name)
            if connection and connection.client:
                connection.client.stop_local_actions()
        self._notify_mcp_listeners()

    def stop_computer_use(self) -> None:
        self._computer_use_stopped = True
        for connection in list(self.mcp_manager.connections.values()):
            if connection.client:
                connection.client.stop_local_actions()
        for agent in list(self._session_agents.values()):
            agent.stop_computer_use()

    def get_key(self) -> str:
        """Kayıtlı API anahtarını işletim sistemi kasasından veya ortamdan okur."""
        return get_api_key(self.provider, self._provider_cfg, base_url=self.base_url)

    def set_key(self, key: str) -> None:
        """API anahtarını güvenli kasaya (Keyring) kaydeder."""
        set_api_key(self.provider, self._provider_cfg, key, base_url=self.base_url)

    def update_settings(self, base_url: str, default_model: str, timeout: float) -> None:
        """Ayarları günceller ve config.yaml dosyasına kaydeder."""
        self.base_url = base_url.strip()
        self.default_model = default_model.strip()
        self.timeout = float(timeout)

        if "providers" not in self.config:
            self.config["providers"] = {}
        if self.provider not in self.config["providers"]:
            self.config["providers"][self.provider] = {}

        self.config["providers"][self.provider]["base_url"] = self.base_url
        self.config["providers"][self.provider]["default_model"] = self.default_model
        self.config["timeout"] = self.timeout
        if self.config_path is not None:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(self.config, self.config_path)

    def set_default_model(self, model: str) -> None:
        """Remember an explicit model choice for future chats and app restarts."""
        model = model.strip()
        if not model:
            raise ValueError("Lütfen bir model seçiniz.")
        if model == self.default_model:
            return
        config = deepcopy(self.config)
        config.setdefault("providers", {}).setdefault(self.provider, {})["default_model"] = model
        save_config(config, self.config_path)
        self._provider_cfg = self.config.setdefault("providers", {}).setdefault(self.provider, {})
        self._provider_cfg["default_model"] = model
        self.default_model = model

    def test_connection_async(
        self,
        on_success: Callable[[Dict[str, Any]], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Sunucu sağlık ve hazırlık kontrolünü arka planda yürütür."""
        def worker():
            async def run():
                async with FirikAPI(self.get_key(), self.base_url, timeout=self.timeout) as client:
                    health = await client.request("GET", "/healthz")
                    ready = await client.request("GET", "/readyz")
                    return {"health": health, "ready": ready}

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, result)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def model_effort_levels(self, model: str) -> List[str]:
        from firik_agent.providers.reasoning import effort_levels
        if self._model_metadata_url != self.base_url:
            return []
        return effort_levels(self._model_metadata.get(model))

    def configure_model_metadata(self, provider) -> None:
        if provider.name == self.provider and provider.base_url.rstrip("/") == self.base_url.rstrip("/"):
            provider.model_metadata = dict(self._model_metadata) if self._model_metadata_url == self.base_url else {}

    def fetch_models_async(
        self,
        on_success: Callable[[List[str]], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Model listesini arka planda çeker."""
        def worker():
            catalogue_url = self.base_url
            async def run():
                async with FirikAPI(self.get_key(), catalogue_url, timeout=self.timeout) as client:
                    data = await client.models()
                    models = []
                    rows = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
                    self._model_metadata = {item["id"]: item for item in rows
                                            if isinstance(item, dict) and item.get("id")}
                    self._model_metadata_url = catalogue_url
                    if isinstance(data, dict) and "data" in data:
                        models = [item.get("id") for item in data["data"] if item.get("id")]
                    elif isinstance(data, list):
                        models = [item.get("id") if isinstance(item, dict) else str(item) for item in data]
                    return models or VARSAYILAN_MODELLER

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                models = loop.run_until_complete(run())
                loop.close()
                self._cached_models = models
                self._dispatch(on_success, models)
            except Exception as e:
                # Ağ hatasında önbellekteki modelleri döner
                self._dispatch(on_success, self._cached_models)
                if on_error:
                    self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def inspect_workspace_async(
        self, path: str | Path, on_success: Callable[[WorkspaceSnapshot], None],
        on_error: Callable[[str], None], before: Optional[WorkspaceSnapshot] = None,
    ) -> None:
        """Read source files off the GUI thread; dispatch results on the GUI thread."""
        def worker():
            try:
                snapshot = capture_workspace(path)
                if before is not None:
                    snapshot.changes = compare_workspaces(before, snapshot)
                self._dispatch(on_success, snapshot)
            except Exception as error:
                self._dispatch(on_error, str(error))

        threading.Thread(target=worker, daemon=True).start()

    def set_coding_editor(self, editor: str, custom_path: str = "") -> None:
        config = dict(self.config, coding_editor={"name": editor, "path": custom_path})
        if self.config_path is not None:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, self.config_path)
        self.config = config

    def fetch_quota_async(
        self,
        on_success: Callable[[Dict[str, Any]], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Kota, bakiye ve şart durumunu arka planda sorgular."""
        def worker():
            async def run():
                key = self.get_key()
                if not key:
                    raise ValueError("API anahtarı bulunamadı. Lütfen Ayarlar sekmesinden anahtarınızı girin.")
                async with FirikAPI(key, self.base_url, timeout=self.timeout) as client:
                    quota = await client.quota()
                    terms = await client.terms_status()
                    return {"quota": quota, "terms": terms}

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                data = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, data)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def fetch_terms_text_async(
        self,
        on_success: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Kullanım şartları metnini çeker."""
        def worker():
            async def run():
                async with FirikAPI(self.get_key(), self.base_url, timeout=self.timeout) as client:
                    res = await client.terms_text()
                    if isinstance(res, dict):
                        return res.get("text", json.dumps(res, ensure_ascii=False, indent=2))
                    return str(res)

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                text = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, text)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def accept_terms_async(
        self,
        version: int,
        on_success: Callable[[Any], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Kullanım şartlarını onaylar."""
        def worker():
            async def run():
                async with FirikAPI(self.get_key(), self.base_url, timeout=self.timeout) as client:
                    return await client.accept_terms(version)

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                res = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, res)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_active_stream(self, session_id: Optional[str] = None) -> None:
        """Stop only the requested conversation; no ID stops all at shutdown."""
        with self._stream_lock:
            ids = [session_id] if session_id else list(self._streams)
            for sid in ids:
                if sid in self._streams:
                    self._streams[sid].set()
                if sid in self._stream_futures:
                    self._stream_futures[sid].cancel()
        if session_id is None and self._active_stream_cancel:
            self._active_stream_cancel.set()

    def chat_stream_async(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
        firik_tools: bool,
        on_delta: Callable[[str], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Sohbet yanıtını SSE akışı olarak arka planda üretir."""
        cancel_event = threading.Event()
        self._active_stream_cancel = cancel_event

        def worker():
            accumulated: List[str] = []

            async def run():
                key = self.get_key()
                if not key:
                    raise ValueError("API anahtarı bulunamadı. Lütfen Ayarlar sekmesinden anahtarınızı girin.")

                body: Dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if firik_tools:
                    body["evren_tools"] = True

                async with FirikAPI(key, self.base_url, timeout=self.timeout) as client:
                    async for event in client.stream("/v1/chat/completions", body):
                        if cancel_event.is_set():
                            break
                        data = event.get("data")
                        if not isinstance(data, dict):
                            continue
                        # Delta çıkarımı
                        delta_text = ""
                        for choice in data.get("choices", []):
                            delta_text += choice.get("delta", {}).get("content") or ""
                        if delta_text:
                            accumulated.append(delta_text)
                            self._dispatch(on_delta, delta_text)

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(run())
                loop.close()
                full_text = "".join(accumulated)
                self._dispatch(on_done, full_text)
            except Exception as e:
                if cancel_event.is_set():
                    self._dispatch(on_done, "".join(accumulated))
                else:
                    self._dispatch(on_error, turkce_hata_mesaji(e))
            finally:
                if self._active_stream_cancel is cancel_event:
                    self._active_stream_cancel = None

        threading.Thread(target=worker, daemon=True).start()

    def ocr_async(
        self,
        model: str,
        image_path: str,
        on_success: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Görselden metin çıkarma (OCR) işlemini arka planda yürütür."""
        def worker():
            async def run():
                key = self.get_key()
                if not key:
                    raise ValueError("API anahtarı bulunamadı. Lütfen Ayarlar sekmesinden anahtarınızı girin.")
                data_url = media_data_url(image_path)
                async with FirikAPI(key, self.base_url, timeout=self.timeout) as client:
                    res = await client.ocr(model=model, image=data_url)
                    if isinstance(res, dict):
                        return res.get("text") or res.get("content") or json.dumps(res, ensure_ascii=False, indent=2)
                    return str(res)

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, result)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def transcribe_async(
        self,
        audio_path: str,
        model: str,
        language: Optional[str],
        response_format: str,
        prompt: str,
        on_success: Callable[[str], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Ses dosyasını çözümleme (Transkripsiyon) işlemini yürütür."""
        def worker():
            async def run():
                key = self.get_key()
                if not key:
                    raise ValueError("API anahtarı bulunamadı. Lütfen Ayarlar sekmesinden anahtarınızı girin.")

                opts: Dict[str, Any] = {"response_format": response_format}
                if language and language != "auto":
                    opts["language"] = language
                if prompt.strip():
                    opts["prompt"] = prompt.strip()

                async with FirikAPI(key, self.base_url, timeout=self.timeout) as client:
                    res = await client.transcribe(filename=audio_path, model=model, **opts)
                    if isinstance(res, str):
                        return res
                    if isinstance(res, dict):
                        return res.get("text") or json.dumps(res, ensure_ascii=False, indent=2)
                    return str(res)

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                result = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, result)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    def rerank_async(
        self,
        model: str,
        query: str,
        documents: List[str],
        on_success: Callable[[List[Dict[str, Any]]], None],
        on_error: Callable[[str], None],
    ) -> None:
        """Dokümanları arama sorgusuna göre yeniden sıralar."""
        def worker():
            async def run():
                key = self.get_key()
                if not key:
                    raise ValueError("API anahtarı bulunamadı. Lütfen Ayarlar sekmesinden anahtarınızı girin.")

                async with FirikAPI(key, self.base_url, timeout=self.timeout) as client:
                    res = await client.rerank(model=model, query=query, documents=documents)
                    results: List[Dict[str, Any]] = []
                    if isinstance(res, dict) and "results" in res:
                        for item in res["results"]:
                            idx = item.get("index", 0)
                            doc_text = documents[idx] if idx < len(documents) else ""
                            results.append({
                                "index": idx,
                                "score": item.get("relevance_score", 0.0),
                                "text": doc_text,
                            })
                    return results

            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                items = loop.run_until_complete(run())
                loop.close()
                self._dispatch(on_success, items)
            except Exception as e:
                self._dispatch(on_error, turkce_hata_mesaji(e))

        threading.Thread(target=worker, daemon=True).start()

    # --- MCP & Session Management ---

    def add_mcp_listener(self, callback: Callable[[], None]) -> None:
        self._mcp_listeners.append(callback)

    def _notify_mcp_listeners(self) -> None:
        for cb in self._mcp_listeners:
            try:
                self._dispatch(cb)
            except Exception:
                pass

    def get_agent(self, session_id: Optional[str] = None) -> Agent:
        """Keep providers, plugins and working directories local to each chat."""
        if session_id:
            if session_id not in self._session_agents:
                config = deepcopy(self.config)
                # The desktop connection pool owns MCP lifecycle and filtering.
                config["mcp_servers"] = {}
                self._session_agents[session_id] = Agent(
                    config=config, config_path=str(self.config_path) if self.config_path else None)
            return self._session_agents[session_id]
        if self.agent is None:
            self.agent = Agent(config=self.config, config_path=str(self.config_path) if self.config_path else None)
            self.agent.tools = self.mcp_manager.tool_registry
            self.session_tool_filter.tool_registry = self.mcp_manager.tool_registry
        return self.agent

    def get_or_create_session(self, session_id: Optional[str] = None) -> ChatSession:
        """Retrieve existing chat session or create a new one with configured defaults."""
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        if session_id:
            record = self.chat_history.load(session_id)
            if record:
                session = self.chat_history.restore_session(record)
                session.active_mcp_servers.intersection_update(self.mcp_manager.connections)
                self.sessions[session_id] = session
                return session

        default_mcps: Set[str] = set()
        for name, conn in self.mcp_manager.connections.items():
            if conn.config.default_for_chat and conn.config.enabled:
                default_mcps.add(name)

        new_session = ChatSession(
            session_id=session_id,
            model=self.default_model,
            active_mcp_servers=default_mcps,
        )
        self.sessions[new_session.session_id] = new_session
        return new_session

    def save_chat_transcript(self, session: ChatSession, messages: List[Dict[str, Any]]) -> None:
        self.chat_history.save_transcript(session, messages)

    def list_chat_history(self) -> List[Dict[str, Any]]:
        return self.chat_history.list_conversations()

    def load_chat_transcript(self, session_id: str) -> List[Dict[str, Any]]:
        record = self.chat_history.load(session_id)
        return record["transcript"] if record else []

    def restore_chat_session(self, session_id: str) -> ChatSession:
        if session_id in self.sessions:
            return self.sessions[session_id]
        record = self.chat_history.load(session_id)
        if record is None:
            raise KeyError(session_id)
        session = self.chat_history.restore_session(record)
        session.active_mcp_servers.intersection_update(self.mcp_manager.connections)
        self.sessions[session_id] = session
        return session

    def delete_chat_history(self, session_id: str) -> None:
        self.cancel_active_stream(session_id)
        self.chat_history.delete(session_id)
        self.sessions.pop(session_id, None)
        self._session_agents.pop(session_id, None)

    def set_session_mcp(self, session_id: str, server_name: str, enabled: bool) -> None:
        """Enable or disable an MCP server strictly for the specified chat session."""
        session = self.get_or_create_session(session_id)
        if enabled:
            session.enable_mcp(server_name)
            self.runtime.submit(self.mcp_manager.acquire_for_session(session_id, server_name))
        else:
            session.disable_mcp(server_name)
            self.runtime.submit(self.mcp_manager.release_for_session(session_id, server_name))
        self.chat_history.save_context(session)
        self.chat_history.save_settings(session)
        self._notify_mcp_listeners()

    def get_session_mcps(self, session_id: str) -> List[str]:
        session = self.get_or_create_session(session_id)
        return list(session.active_mcp_servers)

    def get_mcp_servers(self) -> List[Dict[str, Any]]:
        """Return structured status of all configured MCP servers."""
        return self.mcp_manager.list_status()

    def connect_mcp_async(
        self,
        name: str,
        on_success: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run():
            await self.mcp_manager.connect_server(name)

        def _done(_):
            self._notify_mcp_listeners()
            if on_success:
                self._dispatch(on_success)

        def _err(e: Exception):
            self._notify_mcp_listeners()
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def disconnect_mcp_async(
        self,
        name: str,
        force: bool = True,
        on_success: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run():
            await self.mcp_manager.disconnect_server(name, force=force)

        def _done(_):
            self._notify_mcp_listeners()
            if on_success:
                self._dispatch(on_success)

        def _err(e: Exception):
            self._notify_mcp_listeners()
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def restart_mcp_async(
        self,
        name: str,
        on_success: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run():
            conn = self.mcp_manager.get_connection(name)
            if conn and conn.config.command == "builtin:computer-use":
                self._computer_use_stopped = False
            await self.mcp_manager.restart_server(name)

        def _done(_):
            self._notify_mcp_listeners()
            if on_success:
                self._dispatch(on_success)

        def _err(e: Exception):
            self._notify_mcp_listeners()
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def test_mcp_async(
        self,
        name: str,
        temp_config: Optional[MCPServerConfig] = None,
        on_success: Optional[Callable[[MCPTestResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run() -> MCPTestResult:
            return await self.mcp_manager.test_server(name, temp_config=temp_config)

        def _done(res: MCPTestResult):
            if on_success:
                self._dispatch(on_success, res)

        def _err(e: Exception):
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def add_mcp_server_async(
        self,
        config: MCPServerConfig,
        connect_now: bool = False,
        on_success: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run():
            # 1. Persist to config.yaml
            add_mcp_server_to_config(config.name, config.to_dict(), str(self.config_path) if self.config_path else None)
            self.config = load_config(self.config_path)
            # 2. Add to runtime pool
            await self.mcp_manager.add_server(config, connect_now=connect_now)

        def _done(_):
            self._notify_mcp_listeners()
            if on_success:
                self._dispatch(on_success)

        def _err(e: Exception):
            self._notify_mcp_listeners()
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def update_mcp_server_async(
        self,
        name: str,
        new_config: MCPServerConfig,
        on_success: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run():
            # 1. Update config.yaml atomically
            cfg = load_config(self.config_path)
            servers = cfg.setdefault("mcp_servers", {})
            if name in servers and name != new_config.name:
                del servers[name]
            servers[new_config.name] = new_config.to_dict()
            save_config(cfg, self.config_path)
            self.config = cfg

            # 2. Update runtime connection manager
            await self.mcp_manager.update_server(name, new_config)

        def _done(_):
            self._notify_mcp_listeners()
            if on_success:
                self._dispatch(on_success)

        def _err(e: Exception):
            self._notify_mcp_listeners()
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def remove_mcp_server_async(
        self,
        name: str,
        on_success: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run() -> bool:
            # 1. Remove from config.yaml
            remove_mcp_server_from_config(name, str(self.config_path) if self.config_path else None)
            self.config = load_config(self.config_path)
            if name == "computer-use":
                disabled = self.config.setdefault("disabled_default_mcps", [])
                if name not in disabled:
                    disabled.append(name)
                save_config(self.config, self.config_path)
            # 2. Remove from runtime manager
            ok = await self.mcp_manager.remove_server(name)
            # 3. Clean up from all active chat sessions
            for s in self.sessions.values():
                s.disable_mcp(name)
                self.chat_history.save_settings(s)
            return ok

        def _done(_):
            self._notify_mcp_listeners()
            if on_success:
                self._dispatch(on_success)

        def _err(e: Exception):
            self._notify_mcp_listeners()
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def get_mcp_tools_async(
        self,
        name: str,
        on_success: Callable[[List[Dict[str, Any]]], None],
        on_error: Optional[Callable[[str], None]] = None,
    ) -> None:
        async def run() -> List[Dict[str, Any]]:
            conn = self.mcp_manager.get_connection(name)
            if not conn:
                raise KeyError(f"MCP server '{name}' not found.")
            tools = await conn.refresh_tools() if conn.status == MCPStatus.CONNECTED else conn.cached_tools
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                    "server": name,
                }
                for t in tools
            ]

        def _done(tools):
            self._dispatch(on_success, tools)

        def _err(e: Exception):
            if on_error:
                self._dispatch(on_error, str(e))

        self.runtime.submit(run(), on_done=_done, on_error=_err)

    def get_mcp_logs(self, name: str) -> List[MCPLogEntry]:
        conn = self.mcp_manager.get_connection(name)
        if not conn:
            return []
        return list(conn.logs)

    def duplicate_mcp_server(self, name: str, new_name: str) -> MCPServerConfig:
        cfg = self.mcp_manager.duplicate_server(name, new_name)
        add_mcp_server_to_config(new_name, cfg.to_dict(), str(self.config_path) if self.config_path else None)
        self.config = load_config(self.config_path)
        self._notify_mcp_listeners()
        return cfg

    def export_mcp_configs(self) -> Dict[str, Any]:
        """Export configured MCP servers with secret values redacted as '<SECRET_REQUIRED>'."""
        servers = get_mcp_servers_from_config(self.config_path)
        exported = {}
        for name, conf in servers.items():
            c = dict(conf)
            if "secret_env" in c and isinstance(c["secret_env"], dict):
                c["secret_env"] = {k: "<SECRET_REQUIRED>" for k in c["secret_env"]}
            exported[name] = c
        return exported

    def import_mcp_configs(self, configs: Dict[str, Any]) -> int:
        """Import MCP server configurations."""
        count = 0
        for name, conf in configs.items():
            if isinstance(conf, dict):
                cfg = MCPServerConfig.from_dict(name, conf)
                add_mcp_server_to_config(name, cfg.to_dict(), str(self.config_path) if self.config_path else None)
                count += 1
        self.config = load_config(self.config_path)
        self.mcp_manager.load_from_config(self.config.get("mcp_servers", {}))
        self._notify_mcp_listeners()
        return count

    # --- Desktop Chat Agent Bridge ---

    def chat_agent_stream_async(
        self,
        session_id: str,
        prompt: str,
        image_path: Optional[str] = None,
        cwd: Optional[str] = None,
        on_event: Optional[Callable[[AgentEvent], None]] = None,
        on_done: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        coding_options: Optional[Dict[str, Any]] = None,
    ) -> Future:
        """
        Desktop Chat bridge executing the real Agent loop with streaming events and session-scoped MCP tools.
        """
        if coding_options is not None:
            return self._coding_stream_async(session_id, prompt, cwd, on_event, on_done, on_error, coding_options)
        cancel_event = threading.Event()
        with self._stream_lock:
            if session_id in self._streams and not self._streams[session_id].is_set():
                raise ValueError("Bu sohbette bir yanıt zaten hazırlanıyor.")
            self._streams[session_id] = cancel_event

        def release_stream(_=None):
            with self._stream_lock:
                if self._streams.get(session_id) is cancel_event:
                    self._streams.pop(session_id, None)
                    self._stream_futures.pop(session_id, None)

        async def run():
            session = self.get_or_create_session(session_id)
            agent = self.get_agent(session_id)
            agent.default_cwd = cwd
            await agent.initialize()
            agent.providers.get_active().set_model(session.model)
            self.configure_model_metadata(agent.providers.get_active())
            tool_filter = SessionToolFilter(agent.tools, self.mcp_manager, self.mcp_manager.tool_registry)

            # Acquire/connect active MCPs for this session
            for s_name in list(session.active_mcp_servers):
                try:
                    if on_event:
                        self._dispatch(on_event, AgentEvent(
                            type=AgentEventType.MCP_CONNECTING,
                            session_id=session.session_id, server_name=s_name))
                    acquired = await self.mcp_manager.acquire_for_session(session.session_id, s_name)
                    if not acquired:
                        continue
                    if on_event:
                        self._dispatch(
                            on_event,
                            AgentEvent(
                                type=AgentEventType.MCP_CONNECTED,
                                session_id=session.session_id,
                                server_name=s_name,
                                content=f"MCP '{s_name}' connected.",
                            ),
                        )
                except Exception as ex:
                    logger.warning("Failed connecting MCP '%s' for session: %s", s_name, ex)
                    if on_event:
                        self._dispatch(
                            on_event,
                            AgentEvent(
                                type=AgentEventType.MCP_ERROR,
                                session_id=session.session_id,
                                server_name=s_name,
                                content=str(ex),
                            ),
                        )

            # Vision / Multimodal augmentation if attached image
            augmented_prompt = prompt
            if image_path and os.path.exists(image_path):
                augmented_prompt = f"[User attached image at: {image_path}]\n{prompt}"

            final_text = ""
            context_saved = False
            try:
                async for evt in agent.run_stream(
                    prompt=augmented_prompt,
                    session=session,
                    session_tool_filter=tool_filter,
                    cancel_event=cancel_event,
                ):
                    if evt.type == AgentEventType.DONE:
                        # Finish persistence and release the session before the
                        # UI enables Send. A completion callback may start the
                        # next question immediately in this same conversation.
                        await asyncio.to_thread(self.chat_history.save_context, session)
                        context_saved = True
                        release_stream()
                    if on_event:
                        self._dispatch(on_event, evt)
                    if evt.type == AgentEventType.DONE:
                        final_text = evt.content or ""
            finally:
                if not context_saved:
                    await asyncio.to_thread(self.chat_history.save_context, session)

            if on_done:
                self._dispatch(on_done, final_text)

        def _err(e: Exception):
            release_stream()
            if on_error:
                self._dispatch(on_error, str(e))
            elif on_done:
                self._dispatch(on_done, f"Hata: {e}")

        future = self.runtime.submit(run(), on_error=_err)
        with self._stream_lock:
            if self._streams.get(session_id) is cancel_event:
                self._stream_futures[session_id] = future

        future.add_done_callback(release_stream)
        return future

    def shutdown(self) -> None:
        """Cleanly shutdown runtime and all MCP child processes."""
        self.cancel_active_stream()
        try:
            self.runtime.run_sync(self.mcp_manager.shutdown(), timeout=5.0)
        except Exception:
            pass
        self.runtime.shutdown(wait=True)
