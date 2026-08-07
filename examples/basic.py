"""Basic example: Load a model, generate text, eject it."""

import sys
sys.path.insert(0, "/Users/fehmicitiloglu/Documents/hf-llm-agent")

from hf_llm_agent import LLMAgent


def basic_usage() -> None:
    """Simple example of loading, generating, and ejecting a model."""

    print("=" * 60)
    print("Basic LLM Agent Example")
    print("=" * 60)

    # --- Step 1: Search for models ---
    print("\n🔍 Searching for text generation models...")
    models = LLMAgent().search_models(query="instruction", task="text-generation", limit=5)

    for i, model in enumerate(models[:3], 1):
        print(f"  {i}. {model['id']} ({model.get('downloads', 'N/A')} downloads)")

    # --- Step 2: Load a model ---
    print("\n⏳ Loading Mistral-7B-Instruct-v0.1...")
    agent = LLMAgent(
        model_id="mistralai/Mistral-7B-Instruct-v0.1",
        device="auto",  # Automatically select best available device
    )

    print(f"✅ Agent created: {agent}")
    print(f"   Status: {agent.get_status()}")

    # --- Step 3: Generate text ---
    print("\n🤖 Generating response...")
    prompt = "Explain quantum computing in 3 paragraphs."

    try:
        response = agent.generate(prompt)
        print(f"\n📝 Prompt: {prompt}")
        print(f"📄 Response: {response['outputs'][0]['generated_text'][:200]}...")
        print(f"⏱️  Inference time: {response['inference_time_seconds']:.2f}s")

    except Exception as e:
        print(f"❌ Error generating: {e}")

    # --- Step 4: Eject model to free memory ---
    print("\n🗑️ Ejecting model to free memory...")
    success = agent.eject()
    print(f"✅ Model ejected: {success}")

    # --- Step 5: Verify model is gone ---
    print(f"\n📊 Current status: {agent.get_status()}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    basic_usage()
