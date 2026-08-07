#!/usr/bin/env python3
"""CLI entry point for HF-LLM Agent."""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "/Users/fehmicitiloglu/Documents/hf-llm-agent")


def main() -> None:
    """Main CLI entry point."""

    parser = argparse.ArgumentParser(
        description="HF-LLM Agent - Dynamic Hugging Face Model Manager"
    )

    parser.add_argument(
        "--action",
        choices=["search", "load", "generate", "eject", "status", "list_presets"],
        default="help",
    )

    parser.add_argument(
        "--model",
        help="Model ID or preset name (e.g., 'gemma-2b' or 'mistralai/Mistral-7B-Instruct-v0.1')",
    )

    parser.add_argument(
        "--prompt",
        help="Text prompt for generation",
    )

    parser.add_argument(
        "--category",
        choices=["small", "medium", "large"],
        help="Filter presets by category (for list_presets)",
    )

    parser.add_argument(
        "--query",
        help="Search query for models (for search)",
    )

    args = parser.parse_args()

    if args.action == "help" or not args.action:
        parser.print_help()
        return

    # Import after parsing to avoid issues if no args
    from hf_llm_agent import LLMAgent

    agent = LLMAgent()

    if args.action == "list_presets":
        from models.presets import list_presets

        presets = list_presets(category=args.category)
        print(f"\n=== Available Presets ({len(presets)} total) ===\n")

        for name, config in presets.items():
            print(f"• {name}")
            print(f"  Model: {config['model_id']}")
            print(f"  Description: {config.get('description', 'N/A')}")
        print()

    elif args.action == "search":
        models = agent.search_models(query=args.query or "")

        print(f"\n=== Search Results ({len(models)} total) ===\n")
        for i, model in enumerate(models[:10], 1):
            print(f"{i}. {model['id']}")
            if model.get('downloads'):
                print(f"   Downloads: {model['downloads']}")

    elif args.action == "load":
        if not args.model:
            print("Error: --model is required for loading")
            sys.exit(1)

        try:
            agent.load(model_id=args.model)
            print(f"✅ Successfully loaded model: {args.model}")
            print(f"Status: {agent.get_status()}")

        except Exception as e:
            print(f"❌ Failed to load model: {e}")

    elif args.action == "generate":
        if not args.model and not agent.loaded_models:
            print("Error: Load a model first or provide --model")
            sys.exit(1)

        if not args.prompt:
            print("Error: --prompt is required for generation")
            sys.exit(1)

        try:
            response = agent.generate(model_id=args.model, prompt=args.prompt)
            print(f"\nPrompt: {args.prompt}")
            print(f"Response: {response['outputs'][0]['generated_text']}")
            print(f"Inference time: {response['inference_time_seconds']:.2f}s")

        except Exception as e:
            print(f"❌ Generation failed: {e}")

    elif args.action == "eject":
        if not args.model and agent.loaded_models:
            model = agent.loaded_models[0]
        elif not args.model:
            print("Error: --model is required or load a model first")
            sys.exit(1)

        try:
            agent.eject(model_id=args.model)
            print(f"✅ Successfully ejected model: {args.model}")

        except Exception as e:
            print(f"❌ Failed to eject model: {e}")

    elif args.action == "status":
        print(f"\n=== Agent Status ===")
        print(agent.get_status())


if __name__ == "__main__":
    main()
