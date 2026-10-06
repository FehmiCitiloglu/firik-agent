"""Token usage tracking plugin (per-chat counters, estimates, context warnings)."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from firik_agent.core.types import Message, ToolDefinition
from firik_agent.plugins.base import BasePlugin

try:
    import tiktoken
except Exception:
    tiktoken = None


CONTEXT_WINDOWS = {
    "gpt-4o": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4": 8192,
    "gpt-3.5-turbo": 16385,
    "claude-3-5-sonnet": 200000,
    "claude-3-opus": 200000,
    "gemini-1.5-pro": 2000000,
    "llama-3.1-70b": 131072,
    "firik-large": 32768,
}

DEFAULT_CONTEXT_WINDOW = 8192


class TokenCounterPlugin(BasePlugin):
    """Track cumulative token usage across chats.

    Two counting modes:
      * exact  - caller passes provider-reported ``usage`` numbers;
      * estimate - ``tiktoken`` when available, char-heuristic otherwise.
    Hooks ``on_user_message`` / ``on_model_response`` also auto-record
    estimated usage for the ``"auto"`` chat bucket.
    """

    name = "token_counter"
    description = "Per-chat token usage tracker with context-window warnings"
    version = "1.0.0"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._chats: Dict[str, Dict[str, Any]] = {}
        self._encoding = None
        if tiktoken is not None:
            try:
                self._encoding = tiktoken.get_encoding("cl100k_base")
            except Exception:
                self._encoding = None

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _ensure_chat(self, chat_id: str) -> Dict[str, Any]:
        """Return (creating if needed) the stats dict for a chat."""
        if chat_id not in self._chats:
            self._chats[chat_id] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "turns": 0,
                "exact_turns": 0,
                "estimated_turns": 0,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
        return self._chats[chat_id]

    def estimate_tokens(self, text: str) -> int:
        """Estimate the token count of text."""
        if not text:
            return 0
        if self._encoding is not None:
            try:
                return len(self._encoding.encode(text, disallowed_special=()))
            except Exception:
                pass
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        rest = len(text) - cjk
        return max(1, round(cjk / 1.5 + rest / 4))

    @staticmethod
    def _resolve_window(model: Optional[str]) -> int:
        if not model:
            return DEFAULT_CONTEXT_WINDOW
        key = model.lower()
        for name, size in CONTEXT_WINDOWS.items():
            if name in key:
                return size
        return DEFAULT_CONTEXT_WINDOW

    # ------------------------------------------------------------------ #
    # lifecycle hooks (auto estimation)
    # ------------------------------------------------------------------ #
    async def on_user_message(self, text: str) -> Optional[str]:
        """Auto-count incoming user text as prompt tokens."""
        stats = self._ensure_chat("auto")
        tok = self.estimate_tokens(text)
        stats["prompt_tokens"] += tok
        stats["total_tokens"] += tok
        stats["turns"] += 1
        stats["estimated_turns"] += 1
        stats["updated_at"] = time.time()
        return None

    async def on_model_response(self, message: Message) -> None:
        """Auto-count assistant output as completion tokens."""
        if message.role != "assistant" or not message.content:
            return
        stats = self._ensure_chat("auto")
        tok = self.estimate_tokens(message.content)
        stats["completion_tokens"] += tok
        stats["total_tokens"] += tok
        stats["estimated_turns"] += 1
        stats["updated_at"] = time.time()

    # ------------------------------------------------------------------ #
    # public API (also exposed as agent tools)
    # ------------------------------------------------------------------ #
    def record_usage(
        self,
        chat_id: str = "default",
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        prompt_text: Optional[str] = None,
        completion_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record one exchange; exact provider usage wins over estimates."""
        p = prompt_tokens if prompt_tokens is not None else (
            self.estimate_tokens(prompt_text) if prompt_text else 0)
        c = completion_tokens if completion_tokens is not None else (
            self.estimate_tokens(completion_text) if completion_text else 0)
        t = total_tokens if total_tokens is not None else p + c

        stats = self._ensure_chat(chat_id)
        stats["prompt_tokens"] += p
        stats["completion_tokens"] += c
        stats["total_tokens"] += t
        stats["turns"] += 1
        exact = prompt_tokens is not None or completion_tokens is not None
        stats["exact_turns" if exact else "estimated_turns"] += 1
        stats["updated_at"] = time.time()
        return {"recorded": True,
                "this_turn": {"prompt": p, "completion": c, "total": t},
                "chat_total": stats["total_tokens"]}

    def get_token_usage(self, chat_id: str = "default") -> Dict[str, Any]:
        """Return cumulative usage statistics for a chat."""
        return {"chat_id": chat_id, **self._ensure_chat(chat_id)}

    def list_chats(self) -> List[Dict[str, Any]]:
        """List every tracked chat sorted by token consumption."""
        return [{"chat_id": cid, "total_tokens": s["total_tokens"], "turns": s["turns"]}
                for cid, s in sorted(self._chats.items(),
                                     key=lambda kv: kv[1]["total_tokens"],
                                     reverse=True)]

    def reset_counter(self, chat_id: str = "default") -> Dict[str, Any]:
        """Reset counters for one chat, or all chats when chat_id is '*'."""
        if chat_id == "*":
            self._chats.clear()
            return {"reset": "all"}
        self._chats.pop(chat_id, None)
        return {"reset": chat_id}

    def check_context_window(self, chat_id: str = "default",
                             model: Optional[str] = None) -> Dict[str, Any]:
        """Compare current usage against the model's context window."""
        stats = self._ensure_chat(chat_id)
        window = self._resolve_window(model)
        used = stats["total_tokens"]
        ratio = used / window if window else 0.0
        warning = None
        if ratio >= 0.95:
            warning = "KRITIK: baglam penceresi neredeyse doldu!"
        elif ratio >= 0.80:
            warning = "UYARI: baglam penceresinin %80'i doldu."
        return {"chat_id": chat_id, "model": model or "unknown",
                "used_tokens": used, "context_window": window,
                "usage_ratio": round(ratio, 4),
                "remaining_tokens": max(0, window - used),
                "warning": warning}

    def count_text_tokens(self, text: str) -> Dict[str, Any]:
        """Estimate the token count of an arbitrary text blob."""
        return {"chars": len(text),
                "estimated_tokens": self.estimate_tokens(text),
                "method": "tiktoken" if self._encoding else "heuristic"}

    # ------------------------------------------------------------------ #
    # tool exposure
    # ------------------------------------------------------------------ #
    def get_tools(self) -> List[Tuple[ToolDefinition, Callable[..., Any]]]:
        """Expose the counter API as agent tools."""
        def _wrap(fn):
            def inner(**kwargs: Any) -> str:
                import json
                try:
                    return json.dumps(fn(**kwargs), ensure_ascii=False, default=str)
                except Exception as e:
                    return json.dumps({"error": str(e)})
            return inner

        defs = [
            ToolDefinition(
                name="get_token_usage",
                description="Return cumulative token usage (prompt/completion/total, turns) for a chat.",
                parameters={"type": "object", "properties": {
                    "chat_id": {"type": "string", "description": "Chat identifier (default: 'default')"}}},
                source="plugin:token_counter",
            ),
            ToolDefinition(
                name="record_usage",
                description="Record one turn's token usage. Pass provider 'usage' numbers for exact counts, or texts to estimate.",
                parameters={"type": "object", "properties": {
                    "chat_id": {"type": "string"},
                    "prompt_tokens": {"type": "integer"},
                    "completion_tokens": {"type": "integer"},
                    "total_tokens": {"type": "integer"},
                    "prompt_text": {"type": "string"},
                    "completion_text": {"type": "string"}}},
                source="plugin:token_counter",
            ),
            ToolDefinition(
                name="count_text_tokens",
                description="Estimate the token count of an arbitrary text (uses tiktoken when available).",
                parameters={"type": "object", "properties": {
                    "text": {"type": "string", "description": "Text to measure"}},
                    "required": ["text"]},
                source="plugin:token_counter",
            ),
            ToolDefinition(
                name="check_context_window",
                description="Show how much of the model's context window is consumed and warn near limits.",
                parameters={"type": "object", "properties": {
                    "chat_id": {"type": "string"},
                    "model": {"type": "string", "description": "Model id to resolve the context window"}}},
                source="plugin:token_counter",
            ),
            ToolDefinition(
                name="list_token_chats",
                description="List all tracked chats with their total token consumption.",
                parameters={"type": "object", "properties": {}},
                source="plugin:token_counter",
            ),
            ToolDefinition(
                name="reset_token_counter",
                description="Reset token counters for one chat, or all chats when chat_id is '*'.",
                parameters={"type": "object", "properties": {
                    "chat_id": {"type": "string"}}},
                source="plugin:token_counter",
            ),
        ]
        fns = [
            _wrap(self.get_token_usage),
            _wrap(self.record_usage),
            _wrap(self.count_text_tokens),
            _wrap(self.check_context_window),
            _wrap(lambda: self.list_chats()),
            _wrap(self.reset_counter),
        ]
        return list(zip(defs, fns))