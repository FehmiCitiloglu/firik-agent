from firik_agent.registry import ModelRegistry, ModelStatus, ModelUsageStats


def test_registry_tracks_models_and_usage() -> None:
    registry = ModelRegistry()
    metadata = registry.register("test/model", temperature=0.2)
    metadata.status = ModelStatus.LOADED
    metadata.usage_stats.update(tokens=10, inference_time=2.0)

    assert registry.get_loaded_models() == [metadata]
    assert metadata.to_dict()["status"] == "loaded"
    assert metadata.usage_stats.average_inference_time == 2.0


def test_empty_usage_average_is_zero() -> None:
    assert ModelUsageStats().average_inference_time == 0.0
