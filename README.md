# HF-LLM Agent

A Python-based LLM agent system that can dynamically **load, use, and eject models** from Hugging Face Hub.

## Features

- 🔌 **Dynamic Model Loading** - Load any model from Hugging Face Hub on-demand
- 🗑️ **Model Ejection** - Unload models to free up memory/resources
- 🔄 **Hot-Swapping** - Swap between different models seamlessly
- 📊 **Model Registry** - Track loaded models, their configs, and usage stats
- ⚡ **Memory Management** - Monitor GPU/CPU memory usage
- 🧠 **Agent Framework** - Chain multiple models with different specializations

## Architecture

```
hf-llm-agent/
├── core/               # Core agent and model management logic
│   ├── __init__.py     # Package exports
│   ├── agent.py        # Main LLM Agent class
│   ├── model_manager.py# Model lifecycle (load/eject/clear)
│   └── registry.py     # Model metadata tracking
├── models/             # Pre-configured model presets
│   ├── __init__.py     # Package exports
│   └── presets.py      # Popular models with configs
├── utils/              # Utility functions
│   ├── __init__.py     # Package exports
│   └── memory.py       # Memory monitoring utilities
├── examples/           # Usage examples
│   ├── basic.py        # Simple model loading example
│   ├── multi_model.py  # Multi-model agent chaining
│   └── memory_demo.py  # Memory usage demonstration
├── tests/              # Test suite
│   ├── __init__.py     # Package exports
│   └── test_model_manager.py  # Unit tests
├── .gitignore          # Git ignore rules
├── pyproject.toml      # Project configuration
└── README.md           # This file
```

## Quick Start

### Installation

```bash
# Install dependencies
pip install -e .
# or: pip install huggingface_hub transformers torch accelerate

# Or install dependencies manually
pip install huggingface_hub transformers torch accelerate
```

### Basic Usage

```python
from hf_llm_agent import LLMAgent

# Create agent with automatic device selection
agent = LLMAgent(
    model_id="mistralai/Mistral-7B-Instruct-v0.1",
    max_model_length=4096,
    device="auto"  # 'cuda', 'cpu', or 'mps'
)

# Generate text
response = agent.generate("Explain quantum computing in 3 paragraphs")
print(response['outputs'][0]['generated_text'])

# Eject model to free memory
agent.eject()
```

### Using Model Presets

```python
from hf_llm_agent import LLMAgent
from models.presets import get_preset

# Load using preset name
agent = LLMAgent(preset="gemma-2b")

# Or get config and customize
config = get_preset("llama3.1-8b-instruct")
agent = LLMAgent(
    model_id=config["model_id"],
    max_model_length=config["max_model_length"],
    temperature=config["temperature"],
)
```

### Search Models

```python
from hf_llm_agent import LLMAgent

agent = LLMAgent()

# Search for models
models = agent.search_models(
    query="instruction",
    task="text-generation",
    limit=5,
)

for model in models:
    print(f"{model['id']} - {model.get('downloads', 'N/A')} downloads")
```

### Multi-Model Agent

```python
from hf_llm_agent import LLMAgent

# Create agent with first model (small/fast)
agent = LLMAgent(preset="gemma-2b")

# Quick chat with small model
response = agent.generate("What's your favorite programming language?")

# Eject and load larger model
agent.eject()
agent.load(preset="llama3.1-8b-instruct")

# Complex reasoning with larger model
response = agent.generate("Analyze this Python code...")
```

## Model Lifecycle

1. **Search** - Browse available models on Hugging Face Hub
2. **Load** - Download and load model into memory
3. **Use** - Generate responses using the loaded model
4. **Eject** - Unload model and free resources

## Supported Model Types

- 📝 Text Generation (LLMs)
- 😊 Sentiment Analysis
- 🔍 Text Classification

## Memory Management

### Monitor Memory Usage

```python
from utils.memory import MemoryMonitor

monitor = MemoryMonitor()
print(monitor.report())
```

### Estimate Model Size

```python
from utils.memory import estimate_model_size

size_gb = estimate_model_size("mistralai/Mistral-7B-Instruct-v0.1")
print(f"Estimated size: {size_gb:.2f} GB")
```

### Clear Cache After Ejection

```python
from utils.memory import MemoryMonitor

# After ejecting a model:
MemoryMonitor.clear_cache()
```

## CLI Interface

Run the CLI tool for quick interactions:

```bash
# List available presets
python run.py --action list_presets

# Search for models
python run.py --action search --query "instruction"

# Load a model
python run.py --action load --model gemma-2b

# Generate text
python run.py --action generate --prompt "Explain quantum computing"

# Eject a model
python run.py --action eject --model gemma-2b

# Check status
python run.py --action status
```

## Running Tests

```bash
# Install test dependencies
pip install pytest

# Run all tests
pytest

# Run specific test file
pytest tests/test_model_manager.py -v
```

## Available Presets

### Small Models (< 7B parameters)
- `gemma-2b`: Google's Gemma-2B - efficient and high-quality
- `mistral-7b`: Mistral-7B - excellent balance of quality and speed
- `phi-2`: Microsoft Phi-2 - surprisingly capable
- `tinyllama`: Tiny Llama - tiny model, big results
- `qwen2.5-1b`: Qwen's smallest model

### Medium Models (7B - 13B parameters)
- `gemma-7b`: Google's Gemma-7B
- `llama2-13b`: Llama-2-13B

### Large Models (70B+ parameters)
- `llama2-70b`: Llama-2-70B - state-of-the-art

## Requirements

### System
- Python 3.9+
- CUDA-capable GPU (optional, for faster inference)

### Dependencies
```
huggingface_hub>=0.21.0
transformers>=4.36.0
torch>=2.1.0
accelerate>=0.25.0
```

## Development

### Dev Container

With Docker running, open this repository in VS Code and run
**Dev Containers: Reopen in Container**. The container installs the project and
its development dependencies automatically. Hugging Face downloads are kept in
a named Docker volume so rebuilding the container does not download models
again.

Run the development checks inside the container:

```bash
pytest
ruff check .
black --check .
```

### Code Structure

| Directory | Purpose |
|-----------|---------|
| `core/` | Core agent and model management logic |
| `models/` | Pre-configured model presets |
| `utils/` | Utility functions (memory monitoring, etc.) |
| `examples/` | Usage examples and demos |
| `tests/` | Test suite |

### Adding New Models

1. Create a new preset in `models/presets.py`
2. Add model ID, pipeline type, and configuration parameters
3. Test loading the model with `python run.py --action load --model <preset_name>`

### Adding New Model Types

1. Add support in `core/model_manager.py`'s `_load_model()` method
2. Handle any special requirements for the model type
3. Add tests in `tests/test_model_manager.py`

## License

MIT License - feel free to use and modify as needed.
