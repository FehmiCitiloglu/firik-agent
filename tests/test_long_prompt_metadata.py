"""Headless regressions for context limits from desktop provider catalogues."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from firik_agent.projects.model_capabilities import model_capabilities
from firik_agent.providers.firik import FirikProvider
from firik_agent.ui.service import FirikService


@pytest.mark.parametrize("field", ["context_window", "context_length", "max_model_len"])
@pytest.mark.parametrize("nested", [False, True])
def test_explicit_context_window_aliases_require_provider_evidence(field, nested):
    values = {field: 1_048_576}
    metadata = {"capabilities": values} if nested else values
    assert model_capabilities(metadata)["context_window"] is None
    declared = model_capabilities({**metadata, "source": "provider"})
    assert declared["context_window"] == 1_048_576
    assert declared["verification_status"] == "provider_declared"
    assert model_capabilities(declared)["context_window"] == 1_048_576
    assert model_capabilities({**metadata, "source": "provider", "verified": False})["context_window"] is None


@pytest.mark.parametrize("value", [True, False, "1048576", 1_048_576.0, None, 0, -1, {}, []])
def test_malformed_context_aliases_do_not_establish_capacity(value):
    assert model_capabilities({"source": "provider", "context_length": value})["context_window"] is None


def test_conflicting_total_context_limits_respect_smaller_declaration():
    caps = model_capabilities({"source": "provider", "context_window": 1_048_576, "max_model_len": 32_768,
                               "default_max_output_tokens": 16_384})
    assert caps["context_window"] == 32_768
    assert caps["max_output_tokens"] is None  # A default is not a hard output limit.
    assert model_capabilities({"id": "mimo-v2.6-pro", "source": "provider"})["context_window"] is None


def catalogue_service():
    return SimpleNamespace(
        provider="firik", base_url="https://catalogue.invalid/v1", timeout=1,
        config={"providers": {}}, _model_metadata={}, _model_metadata_url="https://catalogue.invalid/v1",
        _cached_models=["fallback"], get_key=lambda: "test-key", _dispatch=lambda callback, *args: callback(*args))


def test_desktop_catalogue_provenance_enables_large_context_without_gui(monkeypatch):
    # Exercise the actual fetch/transfer boundary without a Tk interpreter,
    # credential lookup, background timing, or network calls.
    data = {"data": [
        {"id": "mimo-v2.6-pro", "context_length": 1_048_576, "default_max_output_tokens": 16_384,
         "capabilities": {"tools": True, "vision": True}},
        {"id": "revoked", "context_length": 1_048_576, "verified": False},
        {"id": "name-only"}, None, "malformed", {"id": []},
    ]}
    original = deepcopy(data)
    calls = []

    class CatalogueAPI:
        def __init__(self, key, base_url, **kwargs):
            calls.append(base_url)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def models(self):
            return data

    class InlineThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr("firik_agent.ui.service.FirikAPI", CatalogueAPI)
    monkeypatch.setattr("firik_agent.ui.service.threading.Thread", InlineThread)
    service = catalogue_service()
    models, errors = [], []
    FirikService.fetch_models_async(service, models.extend, errors.append)
    assert models == ["mimo-v2.6-pro", "revoked", "name-only"]
    assert not errors and calls == [service.base_url]
    assert data == original
    provider = FirikProvider(default_model="mimo-v2.6-pro", base_url=service.base_url)
    FirikService.configure_model_metadata(service, provider)
    assert model_capabilities(provider.model_metadata[provider.current_model])["context_window"] == 1_048_576
    assert model_capabilities(provider.model_metadata["revoked"])["context_window"] is None
    assert model_capabilities(provider.model_metadata["name-only"])["context_window"] is None

    service.base_url = "https://different.invalid/v1"
    provider = FirikProvider(default_model="mimo-v2.6-pro", base_url=service.base_url)
    FirikService.configure_model_metadata(service, provider)
    assert not provider.model_metadata


def test_desktop_explicit_config_can_override_or_revoke_catalogue_capacity():
    service = catalogue_service()
    service._model_metadata = {"selected": {"source": "provider", "context_length": 1_048_576}}
    service.config["providers"] = {"firik": {"model_capabilities": {
        "selected": {"source": "verified_config", "context_window": 32_768}}}}
    provider = FirikProvider(default_model="selected", base_url=service.base_url)
    FirikService.configure_model_metadata(service, provider)
    assert model_capabilities(provider.model_metadata["selected"])["context_window"] == 32_768
    service.config["providers"]["firik"]["model_capabilities"]["selected"] = {"verified": False}
    FirikService.configure_model_metadata(service, provider)
    assert model_capabilities(provider.model_metadata["selected"])["context_window"] is None
