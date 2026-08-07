"""Multi-model example: Use different models for different tasks."""

import sys
sys.path.insert(0, "/Users/fehmicitiloglu/Documents/hf-llm-agent")

from hf_llm_agent import LLMAgent
from models.presets import get_preset, SMALL_MODELS


def multi_model_usage() -> None:
    """Example of using multiple models for different tasks."""

    print("=" * 60)
    print("Multi-Model Agent Example")
    print("=" * 60)

    # --- Step 1: Create agent with first model (small/fast for chat) ---
    print("\n🚀 Loading fast model: Gemma-2B")
    agent = LLMAgent(
        preset="gemma-2b",  # Use small model for fast responses
    )

    print(f"✅ Agent created: {agent}")

    # Quick chat with small model
    print("\n💬 Chat (Gemma-2B):")
    response = agent.generate("What's your favorite programming language? Why?")
    print(f"   Response: {response['outputs'][0]['generated_text'][:150]}...")

    # --- Step 2: Eject small model and load larger one for complex tasks ---
    print("\n🔄 Swapping to larger model for complex reasoning...")
    agent.eject()  # Eject Gemma-2B

    print("⏳ Loading Mistral-7B (more capable)...")
    agent.load(preset="llama3.1-8b-instruct")  # Load larger model

    print(f"✅ Agent updated: {agent}")

    # Complex reasoning with larger model
    print("\n🧠 Reasoning (Mistral-7B):")
    complex_prompt = """Analyze this Python code and explain its time complexity:

def find_duplicates(lst):
    seen = set()
    duplicates = set()
    for item in lst:
        if item in seen:
            duplicates.add(item)
        else:
            seen.add(item)
    return duplicates"""

    try:
        response = agent.generate(complex_prompt)
        print(f"   Response: {response['outputs'][0]['generated_text'][:250]}...")
    except Exception as e:
        print(f"   Error: {e}")

    # --- Step 3: Eject and load sentiment model ---
    print("\n🔄 Swapping to sentiment analysis model...")
    agent.eject()  # Eject Mistral-7B

    print("⏳ Loading sentiment analysis model...")
    # Note: We need to use a different pipeline type for sentiment
    from core.model_manager import ModelManager

    manager = ModelManager(device="auto")
    sentiment_pipe = manager.load_model(
        model_id="distilbert-base-uncased-finetuned-sst-2-english",
        pipeline_type="sentiment-analysis",
    )

    print("✅ Sentiment analysis loaded!")

    # --- Step 4: Use sentiment model ---
    print("\n😊 Sentiment Analysis:")
    sentences = [
        "I love this product! It's amazing!",
        "This is the worst experience ever.",
        "It was okay, nothing special.",
    ]

    for sentence in sentences:
        result = sentiment_pipe(sentence)
        label = result[0]['label']
        score = result[0]['score']
        print(f"   '{sentence[:40]}...' → {label} ({score:.3f})")

    # --- Step 5: Eject sentiment model and show final status ---
    print("\n🗑️ Cleaning up...")
    agent.eject()  # Eject Mistral-7B
    manager.eject_model("distilbert-base-uncased-finetuned-sst-2-english")
    manager.eject_all()

    print(f"\n📊 Final status: {agent.get_status()}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    multi_model_usage()
