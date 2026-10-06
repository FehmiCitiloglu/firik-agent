from __future__ import annotations
import math
from typing import Any, Callable, Dict, List, Tuple
from firik_agent.core.types import ToolDefinition
from firik_agent.plugins.base import BasePlugin


class CalculatorPlugin(BasePlugin):
    """Provides mathematical calculation and scientific formula tools."""

    name = "calculator"
    description = "Provides mathematical expressions evaluation and scientific functions"
    version = "1.0.0"

    def get_tools(self) -> List[Tuple[ToolDefinition, Callable[..., Any]]]:
        tool_def = ToolDefinition(
            name="calculate",
            description="Safely evaluate a mathematical expression (e.g. '2 ** 10', 'math.sqrt(144) * 3', 'sin(pi/2)')",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Mathematical expression to evaluate",
                    }
                },
                "required": ["expression"],
            },
            source="plugin:calculator",
        )

        def calculate(expression: str) -> str:
            # Safe namespace with math functions
            safe_names = {
                "math": math,
                "abs": abs,
                "round": round,
                "min": min,
                "max": max,
                "sum": sum,
                "pow": pow,
            }
            for attr in dir(math):
                if not attr.startswith("_"):
                    safe_names[attr] = getattr(math, attr)

            try:
                # Disallow builtins like exec, eval, __import__
                result = eval(expression, {"__builtins__": {}}, safe_names)
                return str(result)
            except Exception as e:
                return f"Calculation error: {e}"

        return [(tool_def, calculate)]
