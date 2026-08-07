"""Memory demo: Show memory usage before/after loading models."""

import sys
sys.path.insert(0, "/Users/fehmicitiloglu/Documents/hf-llm-agent")

from hf_llm_agent import LLMAgent
from utils.memory import MemoryMonitor, estimate_model_size


def memory_demo() -> None:
    """Demonstrate memory usage with model loading/ejection."""

    print("=" * 60)
    print("Memory Management Demo")
    print("=" * 60)

    # --- Step 1: Check initial memory ---
    print("\n📊 Initial Memory Usage:")
    monitor = MemoryMonitor()
    stats_before = monitor.get_stats()
    print(monitor.report())

    # --- Step 2: Estimate model sizes ---
    print("\n📏 Estimated Model Sizes:")
    models_to_test = ["gemma-2b", "llama3.1-8b-instruct", "llama2-70b"]

    for model_name in models_to_test:
        size_gb = estimate_model_size(model_name)
        print(f"   • {model_name}: ~{size_gb:.1f} GB")

    # --- Step 3: Load a small model and check memory ---
    print("\n⏳ Loading Gemma-2B...")
    agent = LLMAgent(
        preset="gemma-2b",
        device="auto",
    )

    stats_after_small = monitor.get_stats()
    print("\n📊 After Loading Gemma-2B:")
    print(monitor.report())

    # --- Step 4: Eject small model and check memory --
    print("\n🗑️ Ejecting Gemma-2B...")
    agent.eject()

    # Force garbage collection
    import gc
    gc.collect()

    stats_after_eject = monitor.get_stats()
    print("\n📊 After Ejecting Gemma-2B:")
    print(monitor.report())

    # --- Step 5: Show memory comparison ---
    print("\n📈 Memory Comparison:")
    print(f"   Initial:         {stats_before.pymem_current_mb:.2f} MB")
    print(f"   After Loading:   {stats_after_small.pymem_current_mb:.2f} MB")
    print(f"   After Ejecting:  {stats_after_eject.pymem_current_mb:.2f} MB")
    print(f"   Memory Saved:    {stats_after_small.pymem_current_mb - stats_after_eject.pymem_current_mb:.2f} MB")

    # --- Step 6: Show available presets with sizes ---
    print("\n📋 Available Model Presets:")
    from models.presets import SMALL_MODELS, MEDIUM_MODELS, LARGE_MODELS

    for category, models in [("Small", SMALL_MODELS), ("Medium", MEDIUM_MODELS),
                            ("Large", LARGE_MODELS)]:
        print(f"\n   {category} ({len(models)} models):")
        for name, config in list(models.items())[:3]:  # Show first 3 per category
            size = estimate_model_size(config["model_id"])
            print(f"      • {name}: ~{size:.1f} GB")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    memory_demo()
