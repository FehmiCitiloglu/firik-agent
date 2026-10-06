#!/usr/bin/env python3
"""
Dynamic Skills and Plugins Example.
Demonstrates:
1. Dynamically creating, activating, and deactivating a Skill with SKILL.md.
2. Dynamically writing, loading, and executing a Plugin with lifecycle hooks and custom tools.
"""
import asyncio
from firik_agent import Agent
from firik_agent.core.types import ToolCall, ToolCallFunction

async def main():
    agent = Agent()
    await agent.initialize()

    print("=== 1. DYNAMIC SKILL DEMO ===")
    skill = agent.skills.add_skill(
        name="aviation_specialist",
        description="Specialized in unmanned aerial vehicle avionics and sensor systems",
        instructions="Analyze all queries from the perspective of sovereign flight computers and redundant telemetry links.",
        tags=["aviation", "uav", "avionics"],
        activate=True,
    )
    print(f"Created skill: {skill.name} (Active={skill.is_active})")
    print(f"Skill saved at: {skill.source_dir}")

    # Check prompt augmentation
    augmentation = agent.skills.get_prompt_augmentation()
    print("\nPrompt augmentation injected into agent:\n", augmentation)

    print("\n=== 2. DYNAMIC PLUGIN DEMO ===")
    plugin_code = """
from firik_agent.plugins.base import BasePlugin
from firik_agent.core.types import ToolDefinition

class UnitConverterPlugin(BasePlugin):
    name = "unit_converter"
    description = "Converts physical units like knots to km/h, feet to meters"

    def get_tools(self):
        tool_def = ToolDefinition(
            name="knots_to_kmh",
            description="Convert airspeed in knots to kilometers per hour",
            parameters={
                "type": "object",
                "properties": {"knots": {"type": "number", "description": "Speed in knots"}},
                "required": ["knots"]
            },
            source="plugin:unit_converter"
        )
        def convert(knots: float) -> str:
            kmh = round(knots * 1.852, 2)
            return f"{knots} knots = {kmh} km/h"

        return [(tool_def, convert)]

    async def on_tool_call(self, tool_call):
        print(f"[Plugin Hook] Tool requested: {tool_call.function.name}")
"""
    plugin = agent.plugins.load_plugin_from_code("unit_converter", plugin_code)
    print(f"Loaded plugin: {plugin.name}")

    # Test the plugin's tool
    tc = ToolCall(
        id="call_unit_1",
        function=ToolCallFunction(
            name="knots_to_kmh",
            arguments='{"knots": 140}',
        ),
    )
    res = await agent.tools.execute(tc)
    print(f"Tool execution result: {res.content}")

    await agent.close()

if __name__ == "__main__":
    asyncio.run(main())
