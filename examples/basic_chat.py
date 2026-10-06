#!/usr/bin/env python3
"""
Basic chat example using Firik Agent.
Configures provider (direct EVREN or LLMTR gateway) and executes a prompt.
"""
import asyncio
import os
from evren_agent import Agent

async def main():
    agent = Agent()
    await agent.initialize()

    active_provider = agent.providers.get_active()
    print(f"[*] Active provider: {active_provider.name}")
    print(f"[*] Active model:    {active_provider.current_model}")
    print(f"[*] Loaded tools:    {[t.name for t in agent.tools.list_tools()]}")

    prompt = "Introduce yourself and explain what tools and skills you have access to."
    print(f"\nUser > {prompt}\n")

    if not active_provider.api_key:
        print("[!] Note: No API key found in EVREN_API_KEY or LLMTR_API_KEY.")
        print("[!] Please set EVREN_API_KEY in your .env or environment to query live models.")
        await agent.close()
        return

    response = await agent.run(prompt)
    print(f"Agent > {response}")

    await agent.close()

if __name__ == "__main__":
    asyncio.run(main())
