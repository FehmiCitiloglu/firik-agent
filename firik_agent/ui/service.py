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
import weakref
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
    "qwen3.8-flash-next",
    "deepseek-v4.1-flash",
    "mimo-v2.6-pro",
    "gemma-4-31b",
    "qwen3-vl-30b",
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

    def __init__(self, dispatch=None, config_path=None) -> None:
        self._dispatch = dispatch or (lambda callback, *args: callback(*args))
        self._voice_controllers = weakref.WeakSet()
        self._voice_input_tests = weakref.WeakSet()
        self._voice_input_tests_closing = False
        self._voice_settings_listeners = []
        from firik_agent.voice.controller import CaptureCoordinator
        self._voice_capture = CaptureCoordinator()
        from firik_agent.voice.providers import VoiceRequestGate
        self._voice_request_gate = VoiceRequestGate()
        self._voice_catalogue = None
        self._voice_catalogue_url = None
        self._voice_revision = 0
        self.config_path = Path(config_path) if config_path is not None else None
        is_root_cwd = False
        try:
            is_root_cwd = Path.cwd().parent == Path.cwd()
        except Exception:
            pass
        if self.config_path is None and (getattr(sys, "frozen", False) or is_root_cwd):
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
        self.stop_voice()
        self._voice_catalogue = self._voice_catalogue_url = None
        self._voice_revision = getattr(self, "_voice_revision", 0) + 1
        set_api_key(self.provider, self._provider_cfg, key, base_url=self.base_url)

    def update_settings(self, base_url: str, default_model: str, timeout: float) -> None:
        """Ayarları günceller ve config.yaml dosyasına kaydeder."""
        self.stop_voice()
        self._voice_catalogue = self._voice_catalogue_url = None
        self._voice_revision = getattr(self, "_voice_revision", 0) + 1
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
            defaults = getattr(provider, "context_metadata_defaults", lambda: {})()
            provider.model_metadata = {**defaults, **(self._model_metadata
                if self._model_metadata_url == self.base_url else {})}
        # Apply the same exact-model evidence in desktop and registry paths.
        # Endpoint catalogue refreshes cannot silently erase explicit host config.
        configured = self.config.get("providers", {}).get(provider.name, {})
        rows = configured.get("model_capabilities") or configured.get("models") or {}
        if isinstance(rows, list):
            rows = {row["id"]: row for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
        if isinstance(rows, dict):
            from firik_agent.projects.model_capabilities import model_capabilities
            for model, evidence in rows.items():
                if isinstance(evidence, dict) and (isinstance(evidence.get("verified"), bool)
                        or evidence.get("source") in {"provider", "verified_probe", "verified_config"}):
                    provider.model_metadata[model] = {"id": model, **model_capabilities(evidence)}

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
                    if not isinstance(rows, list):
                        rows = []
                    # These rows came from this endpoint's catalogue. Preserve
                    # their provenance so context limits are usable by the same
                    # capability gate as the CLI, including explicit revocations.
                    self._model_metadata = {item["id"]: {**item, "source": "provider"} for item in rows
                                            if isinstance(item, dict) and isinstance(item.get("id"), str)
                                            and item["id"]}
                    self._model_metadata_url = catalogue_url
                    if isinstance(data, dict) and "data" in data:
                        models = list(self._model_metadata)
                    elif isinstance(data, list):
                        models = [item["id"] if isinstance(item, dict) else item for item in rows
                                  if (isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"])
                                  or (isinstance(item, str) and item)]
                    from firik_agent.voice.providers import conversational_models
                    return conversational_models(models, self._model_metadata) or VARSAYILAN_MODELLER

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

    @property
    def voice_settings(self):
        from firik_agent.voice.config import VoiceSettings
        try:
            return VoiceSettings.from_mapping(self.config.get("voice"))
        except ValueError:
            # Malformed optional configuration must never prevent text startup.
            return VoiceSettings()

    def update_voice_settings(self, settings) -> None:
        from firik_agent.voice.config import VoiceSettings
        validated = VoiceSettings.from_mapping(settings.to_dict())
        config = deepcopy(self.config)
        config["voice"] = validated.to_dict()
        save_config(config, self.config_path)
        self.config["voice"] = config["voice"]
        for test in list(getattr(self, "_voice_input_tests", ())):
            test.cancel()
        for controller in list(self._voice_controllers):
            controller.settings = validated
            controller.cancel()
        self._notify_voice_settings_listeners()

    def add_voice_settings_listener(self, callback):
        """Weak listeners refresh mounted voice controls after a settings save."""
        listeners = [reference for reference in getattr(self, "_voice_settings_listeners", ())
                     if reference() is not None]
        if not any(reference() == callback for reference in listeners):
            try:
                listeners.append(weakref.WeakMethod(callback))
            except TypeError:
                listeners.append(weakref.ref(callback))
        self._voice_settings_listeners = listeners

    def remove_voice_settings_listener(self, callback):
        self._voice_settings_listeners = [reference for reference in
            getattr(self, "_voice_settings_listeners", ()) if reference() is not None and reference() != callback]

    def _deliver_voice_settings_listener(self, reference):
        if reference not in getattr(self, "_voice_settings_listeners", ()):
            return
        callback = reference()
        if callback is not None:
            try:
                callback()
            except Exception:
                logger.debug("Ses ayarı dinleyicisi yenilenemedi.")

    def _notify_voice_settings_listeners(self):
        listeners = [reference for reference in getattr(self, "_voice_settings_listeners", ())
                     if reference() is not None]
        self._voice_settings_listeners = listeners
        for reference in listeners:
            self._dispatch(self._deliver_voice_settings_listener, reference)

    @property
    def voice_capabilities(self):
        from firik_agent.voice.providers import evren_capabilities, SystemTextToSpeechProvider
        endpoint, _ = self._voice_connection()
        metadata = (getattr(self, "_voice_catalogue", None)
                    if getattr(self, "_voice_catalogue_url", None) == endpoint else None)
        result = {operation: evren_capabilities(operation, metadata, authenticated=metadata is not None)
                  for operation in ("asr", "tts")}
        if self.voice_settings.tts_provider == "system":
            result["tts"] = SystemTextToSpeechProvider().capabilities()
        return result

    def _voice_connection(self):
        # Scope the credential to the captured Evren endpoint, independently
        # of later UI/provider changes. Never read a live endpoint's key from
        # a request bound to an older endpoint.
        config = deepcopy(self.config.get("providers", {}).get("firik", {}))
        # `firik` owns the configured Evren origin even if an independent LLM
        # provider is selected. Never route microphone audio to that LLM's URL.
        endpoint = config.get("base_url") or (getattr(self, "base_url", DEFAULT_BASE_URL)
            if getattr(self, "provider", "firik") == "firik" else DEFAULT_BASE_URL)
        return endpoint, config

    def discover_voice_capabilities_async(self, on_success, on_error) -> None:
        """Explicit authenticated GET only; never captures or uploads audio."""
        endpoint, credential_config = self._voice_connection()
        revision = getattr(self, "_voice_revision", 0)
        def active():
            return self._voice_connection()[0] == endpoint and getattr(self, "_voice_revision", 0) == revision
        async def discover():
            key = get_api_key("firik", credential_config, base_url=endpoint)
            async with FirikAPI(key, endpoint, timeout=min(self.timeout, 30)) as client:
                result = await client.models()
            rows = result.get("data") if isinstance(result, dict) else result
            if not isinstance(rows, list):
                raise ValueError("Geçersiz model kataloğu.")
            if active():
                self._voice_catalogue, self._voice_catalogue_url = result, endpoint
            return self.voice_capabilities
        from firik_agent.voice.providers import voice_error_message
        # Check again when the UI callback is delivered, not only at HTTP
        # completion, so replaced credentials cannot display stale permissions.
        def deliver(callback, result):
            if active():
                callback(result)
        self.runtime.submit(discover(), on_done=lambda result: self._dispatch(deliver, on_success, result),
            on_error=lambda error: self._dispatch(deliver, on_error, voice_error_message(error)))

    def _voice_provider(self, operation: str, settings=None):
        from firik_agent.voice.providers import (EvrenSpeechToTextProvider, EvrenTextToSpeechProvider,
            SystemTextToSpeechProvider, VoiceRequestGate)
        from firik_agent.voice.errors import VoiceAudioError
        settings = settings or self.voice_settings
        if operation == "tts" and settings.tts_provider == "system":
            return SystemTextToSpeechProvider()
        provider = settings.asr_provider if operation == "asr" else settings.tts_provider
        if provider != "evren":
            raise VoiceAudioError("Seçilen yerel ses çözümleme sağlayıcısı henüz kurulu değil. Evren'i seçin veya metinle devam edin.")
        endpoint, credential_config = self._voice_connection()
        def client_factory():
            key = get_api_key("firik", credential_config, base_url=endpoint)
            if not key:
                raise VoiceAudioError("Evren API anahtarı bulunamadı. Ayarlar bölümünden anahtarınızı girin veya metinle devam edin.")
            return FirikAPI(key, endpoint, timeout=min(self.timeout, 120.0))
        if not hasattr(self, "_voice_request_gate"):
            self._voice_request_gate = VoiceRequestGate()
        cls = EvrenSpeechToTextProvider if operation == "asr" else EvrenTextToSpeechProvider
        return cls(client_factory, endpoint=endpoint, gate=self._voice_request_gate,
                   capabilities=self.voice_capabilities[operation])

    def _create_voice_speaker(self, settings):
        from firik_agent.voice.providers import ProviderSynthesizer, SystemProviderSynthesizer
        from firik_agent.voice.synthesis import EngineSpeaker
        if settings.tts_provider == "system":
            synthesizer = SystemProviderSynthesizer(self._voice_provider("tts", settings), speed=settings.playback_speed)
            return EngineSpeaker(output_device=settings.output_device, volume=settings.output_volume, synthesizer=synthesizer)
        # Evren uses its documented speed multiplier, independently of the
        # session's LLM and the legacy local words/minute control.
        synthesizer = ProviderSynthesizer(self.runtime, self._voice_provider("tts", settings),
            model=settings.tts_model, voice=settings.tts_voice, response_format=settings.tts_format,
            speed=settings.playback_speed)
        return EngineSpeaker(output_device=settings.output_device, volume=settings.output_volume, synthesizer=synthesizer)

    async def _transcribe_microphone(self, audio: bytes, model: str, language: str) -> str:
        from firik_agent.voice.transcription import TranscriptionAdapter, TranscriptionPolicy
        adapter = TranscriptionAdapter(self._transcribe_microphone_segment,
            TranscriptionPolicy(max_request_seconds=self.voice_settings.asr_request_seconds))
        return await adapter.transcribe(audio, model, language)

    async def _transcribe_microphone_segment(self, audio: bytes, model: str, language: str) -> str:
        result = await self._voice_provider("asr").transcribe(audio, model=model, language=language)
        text = result.text
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Konuşma algılanamadı. Lütfen yeniden deneyin.")
        if len(text) > 32000:
            raise ValueError("Transkript çok uzun. Daha kısa bir kayıt yapın.")
        from firik_agent.voice.transcription import FinalTranscript
        return FinalTranscript(text.strip(), getattr(result, "language", None))

    def create_voice_controller(self, *, on_state, on_transcript, on_error, session_id=None, on_level=None):
        from firik_agent.voice.controller import CaptureCoordinator, VoiceController
        if not hasattr(self, "_voice_capture"):
            self._voice_capture = CaptureCoordinator()
        controller = VoiceController(
            runtime=self.runtime, dispatch=self._dispatch,
            transcribe=self._transcribe_microphone, settings=self.voice_settings,
            on_state=on_state, on_transcript=on_transcript, on_error=on_error,
            coordinator=self._voice_capture, session_id=session_id,
            on_level=on_level,
            speaker_factory=self._create_voice_speaker,
        )
        self._voice_controllers.add(controller)
        return controller

    def stop_voice(self) -> None:
        for test in list(getattr(self, "_voice_input_tests", ())):
            test.cancel()
        for controller in list(getattr(self, "_voice_controllers", ())):
            controller.cancel()

    def start_voice_input_test(self, on_level, on_done, on_error, settings=None):
        """Explicit local microphone level check; never transcribes or uploads."""
        from firik_agent.voice.config import VoiceSettings
        from firik_agent.voice.diagnostics import InputLevelTest
        from firik_agent.voice.controller import CaptureCoordinator
        settings = self.voice_settings if settings is None else VoiceSettings.from_mapping(settings.to_dict())
        if not hasattr(self, "_voice_capture"):
            self._voice_capture = CaptureCoordinator()
        if not hasattr(self, "_voice_input_tests"):
            self._voice_input_tests = weakref.WeakSet()
        test = InputLevelTest(coordinator=self._voice_capture, dispatch=self._dispatch,
            settings=settings, on_level=on_level, on_done=on_done, on_error=on_error)
        self._voice_input_tests.add(test)
        if getattr(self, "_voice_input_tests_closing", False):
            return test.reject("Uygulama kapanıyor; mikrofon testi başlatılamaz.")
        return test.start()

    def voice_diagnostics(self):
        """Offline setup facts, without microphone/network/credential access."""
        from firik_agent.voice.diagnostics import voice_diagnostics
        return voice_diagnostics(self.voice_settings)

    def validate_voice_request(self, request) -> None:
        """Resolve only already-owned sessions; voice cannot create an ID here."""
        from firik_agent.voice.execution import voice_metadata
        voice_metadata(request)
        if request.cancellation_token.is_set():
            raise ValueError("Ses isteği iptal edildi.")
        if request.session_id not in self.sessions:
            raise ValueError("Ses isteğinin mevcut sohbeti bulunamadı.")
        if request.intended_target == "coding":
            project = self.projects.db.get_project(request.project_id) if request.project_id else None
            if (not request.project_id or
                    getattr(self, "_coding_session_projects", {}).get(request.session_id) != request.project_id
                    or project is None):
                raise ValueError("Ses isteğinin proje / sohbet kapsamı değişti.")
            if not project.local_path or request.workspace_id != str(Path(project.local_path).resolve()):
                raise ValueError("Ses isteğinin çalışma alanı değişti; yeniden konuşun.")
        elif (request.intended_target != "conversation" or request.project_id is not None
              or request.workspace_id is not None):
            raise ValueError("Geçersiz ses isteği hedefi.")

    def create_voice_request_router(self, **options):
        from firik_agent.voice.intent_router import VoiceRequestRouter
        return VoiceRequestRouter(**options)

    async def _voice_tool_approval(self, payload, callback, cancel_event):
        """Marshal a trusted button decision to the existing asynchronous run."""
        if callback is None or cancel_event.is_set():
            return False
        loop = asyncio.get_running_loop()
        answer = loop.create_future()
        def decide(allowed):
            def resolve():
                if not answer.done():
                    answer.set_result(allowed is True and not cancel_event.is_set())
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(resolve)
                except RuntimeError:
                    pass
        def show():
            if cancel_event.is_set() or answer.done():
                decide(False)
                return
            try:
                callback(payload, decide)
            except Exception:
                decide(False)
        self._dispatch(show)
        try:
            while not cancel_event.is_set():
                done, _ = await asyncio.wait({answer}, timeout=.1)
                if done:
                    return answer.result()
            return False
        finally:
            if not answer.done():
                answer.cancel()

    def audio_devices_async(self, on_done, on_error) -> None:
        """Enumerate optional native devices off Tk, with no microphone opened."""
        def worker():
            try:
                from firik_agent.voice.audio_devices import AudioDeviceCatalog
                catalog = AudioDeviceCatalog()
                devices = {kind: catalog.list_devices(kind) for kind in ("input", "output")}
                self._dispatch(on_done, devices)
            except Exception:
                self._dispatch(on_error, "Ses aygıtları okunamadı; ses paketini ve sistem ayarlarını kontrol edin.")
        threading.Thread(target=worker, name="FirikAudioDevices", daemon=True).start()

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
            if name in {"computer-use", "web"}:
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
        voice_request=None,
        on_approval=None,
    ) -> Future:
        """
        Desktop Chat bridge executing the real Agent loop with streaming events and session-scoped MCP tools.
        """
        if coding_options is not None:
            scoped_request = voice_request if voice_request is not None else coding_options.get("voice_request")
            if scoped_request is not None:
                self.validate_voice_request(scoped_request)
                if scoped_request.session_id != session_id or scoped_request.intended_target != "coding":
                    raise ValueError("Ses isteği bu kodlama oturumuna ait değil.")
            if voice_request is not None:
                coding_options = {**coding_options, "voice_request": voice_request}
            return self._coding_stream_async(session_id, prompt, cwd, on_event, on_done, on_error, coding_options)
        if voice_request is not None:
            self.validate_voice_request(voice_request)
            if voice_request.session_id != session_id or voice_request.intended_target != "conversation":
                raise ValueError("Ses isteği bu sohbete ait değil.")
            if prompt != voice_request.request_text or image_path is not None or cwd is not None:
                raise ValueError("Ses isteğinin incelenen içeriği veya hedefi değiştirilemez.")
        cancel_event = voice_request.cancellation_token if voice_request is not None else threading.Event()
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
            if voice_request is not None:
                from firik_agent.voice.execution import VoiceToolFilter
                tool_filter = VoiceToolFilter(tool_filter, voice_request,
                    approve=lambda payload: self._voice_tool_approval(payload, on_approval, cancel_event),
                    cancel_event=cancel_event,
                    on_event=(lambda event: self._dispatch(on_event, event)) if on_event else None)

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
            message_start = len(session.messages)
            voice_marked = False
            try:
                async for evt in agent.run_stream(
                    prompt=augmented_prompt,
                    session=session,
                    session_tool_filter=tool_filter,
                    cancel_event=cancel_event,
                ):
                    if voice_request is not None:
                        metadata = voice_request.to_metadata()
                        evt.metadata = {**evt.metadata, **metadata}
                        if not voice_marked:
                            for message in session.messages[message_start:]:
                                if message.role == "user":
                                    message.execution.update(metadata)
                                    voice_marked = True
                                    break
                    if evt.type in {AgentEventType.TOOL_CALL_STARTED, AgentEventType.TOOL_CALL_RESULT,
                                    AgentEventType.TOOL_CALL_ERROR}:
                        # Commit attempt identity before advancing the generator
                        # to dispatch, then retain the recorded result/status.
                        await asyncio.to_thread(self.chat_history.save_context, session)
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
        self._voice_input_tests_closing = True
        input_tests = list(getattr(self, "_voice_input_tests", ()))
        for test in input_tests:
            test.cancel()
        for controller in list(self._voice_controllers):
            controller.close()
        for controller in list(self._voice_controllers):
            if hasattr(controller, "shutdown"):
                controller.shutdown(timeout=3.0)
        for test in input_tests:
            test.shutdown(timeout=3.0)
        self.cancel_active_stream()
        try:
            self.runtime.run_sync(self.mcp_manager.shutdown(), timeout=5.0)
        except Exception:
            pass
        self.runtime.shutdown(wait=True)
