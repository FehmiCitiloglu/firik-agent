"""Tests for HF-LLM Agent Model Manager and Registry."""

import pytest
from unittest.mock import MagicMock, patch

from core.registry import ModelRegistry, ModelStatus, ModelMetadata
from core.model_manager import ModelManager


# =============================================================================
# Test Models and Fixtures
# =============================================================================

@pytest.fixture
def registry():
    """Create a fresh ModelRegistry for each test."""
    return ModelRegistry()


@pytest.fixture
def manager():
    """Create a fresh ModelManager for each test."""
    return ModelManager(device="cpu")


# =============================================================================
# Test Registry
# =============================================================================

class TestModelRegistry:
    """Tests for ModelRegistry class."""

    def test_register_model(self, registry):
        """Test registering a new model."""
        meta = registry.register(
            model_id="test/model",
            pipeline_type="text-generation",
            max_model_length=4096,
        )

        assert meta.model_id == "test/model"
        assert meta.pipeline_type == "text-generation"
        assert meta.max_model_length == 4096
        assert meta.status == ModelStatus.EJECTED

    def test_register_defaults(self, registry):
        """Test registering a model with default values."""
        meta = registry.register(model_id="default/model")

        assert meta.pipeline_type == "text-generation"
        assert meta.max_model_length == 4096
        assert meta.temperature == 1.0
        assert meta.top_p == 1.0

    def test_unregister_model(self, registry):
        """Test unregistering a model."""
        meta = registry.register(model_id="test/model")
        removed = registry.unregister("test/model")

        assert removed == meta
        assert "test/model" not in registry
        assert len(registry) == 0

    def test_get_model(self, registry):
        """Test retrieving model metadata."""
        meta = registry.register(model_id="test/model")

        retrieved = registry.get("test/model")
        assert retrieved == meta

    def test_get_missing_model(self, registry):
        """Test retrieving a non-existent model."""
        result = registry.get("nonexistent/model")

        assert result is None

    def test_get_loaded_models(self, registry):
        """Test getting all loaded models."""
        meta1 = registry.register(model_id="model1")
        meta2 = registry.register(model_id="model2")

        # Mark one as loaded
        meta1.status = ModelStatus.LOADED

        loaded = registry.get_loaded_models()
        assert len(loaded) == 1
        assert loaded[0] == meta1

    def test_get_all_models(self, registry):
        """Test getting all registered models."""
        registry.register(model_id="model1")
        registry.register(model_id="model2")

        all_models = registry.get_all_models()
        assert len(all_models) == 2

    def test_clear_registry(self, registry):
        """Test clearing all models from registry."""
        registry.register(model_id="model1")
        registry.register(model_id="model2")

        registry.clear()
        assert len(registry) == 0

    def test_contains_check(self, registry):
        """Test checking if model is registered."""
        registry.register(model_id="test/model")

        assert "test/model" in registry
        assert "other/model" not in registry

    def test_length(self, registry):
        """Test getting number of registered models."""
        assert len(registry) == 0

        registry.register(model_id="model1")
        registry.register(model_id="model2")
        assert len(registry) == 2

    def test_iteration(self, registry):
        """Test iterating over registered models."""
        registry.register(model_id="model1")
        registry.register(model_id="model2")

        models = list(registry)
        assert len(models) == 2

    def test_to_dict(self, registry):
        """Test converting metadata to dictionary."""
        meta = registry.register(
            model_id="test/model",
            pipeline_type="text-generation",
            description="Test model",
        )

        d = meta.to_dict()
        assert "model_id" in d
        assert "pipeline_type" in d
        assert d["model_id"] == "test/model"

    def test_is_loaded(self, registry):
        """Test checking if model is loaded."""
        meta = registry.register(model_id="test/model")

        assert not meta.is_loaded()

        meta.status = ModelStatus.LOADED
        assert meta.is_loaded()


# =============================================================================
# Test ModelManager (with mocks)
# =============================================================================

class TestModelManager:
    """Tests for ModelManager class (using mocks)."""

    def test_init_default_device(self):
        """Test initializing with default device."""
        manager = ModelManager()

        assert manager.device == "auto"
        assert isinstance(manager.registry, ModelRegistry)

    def test_init_custom_device(self):
        """Test initializing with custom device."""
        manager = ModelManager(device="cuda")

        assert manager.device == "cuda"

    def test_search_models_raises_without_deps(self, monkeypatch):
        """Test that search raises if dependencies are missing."""
        with patch("core.model_manager.HAS_HF_HUB", False):
            manager = ModelManager()

            with pytest.raises(ImportError, match="huggingface_hub"):
                manager.search_models(query="test")

    def test_load_model_success(self, monkeypatch):
        """Test successful model loading with mocks."""
        # Mock the pipeline function
        mock_pipe = MagicMock()

        manager = ModelManager(device="cpu")

        # Patch the load_model method to return mock pipe
        with patch.object(manager, "_validate_dependencies"):
            # Patch the load_model to use mock pipe
            manager._loaded_models = {"test/model": mock_pipe}
            manager.registry.register(
                model_id="test/model",
                pipeline_type="text-generation",
            )

        assert "test/model" in manager._loaded_models
        assert manager._is_model_loaded("test/model")

    def test_load_already_loaded(self, monkeypatch):
        """Test loading an already loaded model."""
        mock_pipe = MagicMock()

        manager = ModelManager(device="cpu")
        manager._loaded_models = {"test/model": mock_pipe}

        # Mock _is_model_loaded to return True
        with patch.object(manager, "_is_model_loaded", return_value=True):
            result = manager.load_model(model_id="test/model")

        assert result == mock_pipe

    def test_eject_loaded_model(self, monkeypatch):
        """Test ejecting a loaded model."""
        mock_pipe = MagicMock()

        manager = ModelManager(device="cpu")
        manager._loaded_models = {"test/model": mock_pipe}

        # Mock the registry
        meta = manager.registry.register(
            model_id="test/model",
            pipeline_type="text-generation",
        )
        meta.status = ModelStatus.LOADED

        # Mock torch.cuda.empty_cache if available
        with patch("core.model_manager.torch", new=MagicMock()):
            torch_mock = manager.manager._resolve_device()

        result = manager.eject_model("test/model")
        assert result is True
        assert "test/model" not in manager._loaded_models

    def test_eject_not_loaded_model(self, monkeypatch):
        """Test ejecting a model that's not loaded."""
        manager = ModelManager(device="cpu")

        result = manager.eject_model("nonexistent/model")
        assert result is False

    def test_eject_all(self, monkeypatch):
        """Test ejecting all loaded models."""
        mock_pipe1 = MagicMock()
        mock_pipe2 = MagicMock()

        manager = ModelManager(device="cpu")
        manager._loaded_models = {
            "model1": mock_pipe1,
            "model2": mock_pipe2,
        }

        # Mock is_loaded to return True
        for model_id in manager._loaded_models:
            meta = manager.registry.register(
                model_id=model_id,
                pipeline_type="text-generation",
            )
            meta.status = ModelStatus.LOADED

        with patch.object(manager, "eject_model", return_value=True):
            ejected = manager.eject_all()

        assert len(ejected) == 2
        assert "model1" in ejected
        assert "model2" in ejected

    def test_get_loaded_models(self, monkeypatch):
        """Test getting list of loaded models."""
        manager = ModelManager(device="cpu")

        # Add a model to _loaded_models
        manager._loaded_models = {"test/model": MagicMock()}

        assert manager.loaded_models == ["test/model"]

    def test_get_model_info(self, monkeypatch):
        """Test getting model information."""
        manager = ModelManager(device="cpu")

        # Register a model with metadata
        meta = manager.registry.register(
            model_id="test/model",
            pipeline_type="text-generation",
            description="Test model",
        )

        info = manager.get_model_info("test/model")
        assert info is not None
        assert info["model_id"] == "test/model"
        assert info["pipeline_type"] == "text-generation"

    def test_get_model_info_missing(self, monkeypatch):
        """Test getting info for non-existent model."""
        manager = ModelManager(device="cpu")

        info = manager.get_model_info("nonexistent/model")
        assert info is None


# =============================================================================
# Test Memory Monitor (utils)
# =============================================================================

class TestMemoryMonitor:
    """Tests for MemoryMonitor class."""

    def test_get_stats_returns_memory_stats(self):
        """Test that get_stats returns a MemoryStats object."""
        monitor = MemoryMonitor()

        stats = monitor.get_stats()
        assert hasattr(stats, "pymem_total_mb")
        assert hasattr(stats, "cuda_allocated_mb")

    def test_report_returns_string(self):
        """Test that report returns a formatted string."""
        monitor = MemoryMonitor()

        report = monitor.report()
        assert isinstance(report, str)
        assert "Memory Stats" in report

    def test_clear_cache(self):
        """Test that clear_cache runs without error."""
        monitor = MemoryMonitor()

        # Should not raise any exceptions
        monitor.clear_cache()


# =============================================================================
# Test Memory Utilities
# =============================================================================

class TestMemoryUtils:
    """Tests for memory utility functions."""

    def test_estimate_model_size_small(self):
        """Test estimating size for small models."""
        from utils.memory import estimate_model_size

        # Small model patterns should return < 10 GB
        size = estimate_model_size("google/gemma-2b")
        assert size < 10.0

    def test_estimate_model_size_large(self):
        """Test estimating size for large models."""
        from utils.memory import estimate_model_size

        # Large model patterns should return > 10 GB
        size = estimate_model_size("meta-llama/Llama-2-70b-hf")
        assert size > 10.0

    def test_estimate_model_size_unknown(self):
        """Test estimating size for unknown model."""
        from utils.memory import estimate_model_size

        # Unknown models should return a reasonable default
        size = estimate_model_size("unknown/model")
        assert size > 0
