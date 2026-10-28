from __future__ import annotations
import logging
from typing import Dict, List, Optional
from firik_agent.providers.base import BaseProvider
from firik_agent.providers.firik import FirikProvider
from firik_agent.providers.openai_provider import OpenAIProvider
from firik_agent.credentials import get_api_key

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Central registry managing all LLM providers and active selection."""

    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._active_provider_name: Optional[str] = None

    def register(self, name: str, provider: BaseProvider, set_active: bool = False) -> None:
        self._providers[name] = provider
        if set_active or self._active_provider_name is None:
            self._active_provider_name = name
        logger.info("Registered provider '%s' (active=%s)", name, self._active_provider_name == name)

    def get(self, name: str) -> Optional[BaseProvider]:
        return self._providers.get(name)

    def get_active(self) -> BaseProvider:
        if not self._active_provider_name or self._active_provider_name not in self._providers:
            raise RuntimeError("No active provider registered.")
        return self._providers[self._active_provider_name]

    def set_active(self, name: str) -> BaseProvider:
        if name not in self._providers:
            available = list(self._providers.keys())
            raise ValueError(f"Provider '{name}' not found. Available: {available}")
        self._active_provider_name = name
        logger.info("Active provider switched to '%s'", name)
        return self._providers[name]

    def list_providers(self) -> List[str]:
        return list(self._providers.keys())

    def set_model_capabilities(self, provider_name: str, model: str, evidence: Dict) -> Dict:
        """Record host/provider evidence for one exact provider/model identity.

        Native request features remain isolated in provider adapters; recording
        metadata never enables a feature, changes an endpoint, or grants tools.
        """
        from firik_agent.projects.model_capabilities import model_capabilities
        provider = self.get(provider_name)
        if provider is None or not isinstance(model, str) or not model.strip():
            raise ValueError("Yetenek kaydı için kayıtlı sağlayıcı ve model gereklidir.")
        normalized = model_capabilities(evidence)
        provider.model_metadata[model] = {"id": model, **normalized}
        return normalized

    def capabilities(self, provider_name: str, model: Optional[str] = None) -> Dict:
        from firik_agent.projects.model_capabilities import model_capabilities
        provider = self.get(provider_name)
        if provider is None:
            raise ValueError("Sağlayıcı bulunamadı.")
        return model_capabilities(provider.model_metadata.get(model or provider.current_model))

    @property
    def active_name(self) -> Optional[str]:
        return self._active_provider_name

    @classmethod
    def from_config(cls, config: Dict) -> "ProviderRegistry":
        """Factory creating registry populated from config.yaml."""
        registry = cls()
        providers_conf = config.get("providers", {})

        # 1. FIRIK Direct Provider
        firik_conf = providers_conf.get("firik", {})
        firik_key = get_api_key("firik", firik_conf)
        firik_prov = FirikProvider(
            api_key=firik_key,
            base_url=firik_conf.get("base_url", "https://evren-llmapi.ssyz.org.tr/v1"),
            default_model=firik_conf.get("default_model", "glm-5.3"),
            name="firik",
            is_gateway=False,
        )
        registry.register("firik", firik_prov)

        # 2. LLMTR Gateway Provider (for FIRIK models via LLMTR)
        llmtr_conf = providers_conf.get("llmtr", {})
        llmtr_key = get_api_key("llmtr", llmtr_conf)
        llmtr_prov = FirikProvider(
            api_key=llmtr_key,
            base_url=llmtr_conf.get("base_url", "https://llmtr.com/v1"),
            default_model=llmtr_conf.get("default_model", "evren/glm-5.3-fp8"),
            name="llmtr",
            is_gateway=True,
        )
        registry.register("llmtr", llmtr_prov)

        # 3. OpenAI Provider
        openai_conf = providers_conf.get("openai", {})
        openai_key = get_api_key("openai", openai_conf)
        openai_prov = OpenAIProvider(
            api_key=openai_key,
            base_url=openai_conf.get("base_url", "https://api.openai.com/v1"),
            default_model=openai_conf.get("default_model", "gpt-4o"),
            name="openai",
        )
        registry.register("openai", openai_prov)

        # Opt-in local/OpenAI-compatible servers share the existing provider.
        # A model name never implies tool or context-window capability.
        for name, provider_conf in providers_conf.items():
            if name in registry.list_providers() or not isinstance(provider_conf, dict):
                continue
            if provider_conf.get("type") != "openai_compatible" and name not in {"ollama", "lmstudio"}:
                continue
            defaults = {"ollama": "http://127.0.0.1:11434/v1", "lmstudio": "http://127.0.0.1:1234/v1"}
            base_url = provider_conf.get("base_url", defaults.get(name))
            if not base_url:
                raise ValueError(f"'{name}' sağlayıcısının sunucu adresi belirtilmeli.")
            registry.register(name, OpenAIProvider(
                api_key=get_api_key(name, provider_conf) or "local",
                base_url=base_url, default_model=provider_conf.get("default_model", ""), name=name))

        # Explicit capability evidence is local host configuration. In
        # particular /models names alone do not establish tool/window support.
        for name in registry.list_providers():
            evidence_by_model = providers_conf.get(name, {}).get("model_capabilities", {})
            if isinstance(evidence_by_model, dict):
                for model, evidence in evidence_by_model.items():
                    if isinstance(evidence, dict):
                        registry.set_model_capabilities(name, model, evidence)

        # Set default active
        default_prov = config.get("agent", {}).get("default_provider", "firik")
        if default_prov in registry.list_providers():
            registry.set_active(default_prov)

        return registry
