from evren_agent.providers.registry import ProviderRegistry


def test_opt_in_local_compatible_providers_preserve_existing(monkeypatch):
    monkeypatch.setattr("evren_agent.providers.registry.get_api_key", lambda *args, **kwargs: "")
    registry = ProviderRegistry.from_config({"providers": {
        "ollama": {"default_model": "local-code"},
        "lmstudio": {"default_model": "local-chat"},
        "private": {"type": "openai_compatible", "base_url": "http://127.0.0.1:9911/v1", "default_model": "local"},
    }})
    assert {"evren", "llmtr", "openai", "ollama", "lmstudio", "private"} <= set(registry.list_providers())
    assert registry.active_name == "evren"
    assert registry.get("ollama").base_url == "http://127.0.0.1:11434/v1"
    assert registry.get("lmstudio").current_model == "local-chat"
