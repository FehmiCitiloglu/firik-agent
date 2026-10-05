"""Local desktop conversations: display transcript and resumable agent context."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional

from evren_agent.core.session import ChatSession
from evren_agent.core.types import Message


class ChatHistoryStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at REAL NOT NULL, updated_at REAL NOT NULL,
                transcript TEXT NOT NULL, context TEXT NOT NULL DEFAULT '{}', settings TEXT NOT NULL DEFAULT '{}'
            )""")
            if "settings" not in {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}:
                conn.execute("ALTER TABLE conversations ADD COLUMN settings TEXT NOT NULL DEFAULT '{}'")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _context(session: ChatSession) -> Dict[str, Any]:
        return {
            "model": session.model, "system_prompt": session.system_prompt,
            "temperature": session.temperature, "max_tokens": session.max_tokens,
            "reasoning_effort": session.reasoning_effort,
            "active_mcp_servers": sorted(session.active_mcp_servers),
            "enabled_tool_names": sorted(session.enabled_tool_names) if session.enabled_tool_names is not None else None,
            "messages": [message.model_dump(mode="json") for message in list(session.messages)],
        }

    def save_transcript(self, session: ChatSession, transcript: List[Dict[str, Any]]) -> None:
        if not transcript:
            return
        first_user = next((row for row in transcript if row.get("role") == "user"), {})
        content = first_user.get("content", "")
        if isinstance(content, list):
            content = " ".join(part.get("text", "") for part in content if part.get("type") == "text")
        session.title = " ".join(str(content).split())[:70] or "Görsel sohbeti"
        context = self._context(session)
        settings = {key: value for key, value in context.items() if key != "messages"}
        with closing(self._connect()) as conn, conn:
            # UI saves never overwrite the independently updated runtime context.
            conn.execute("""INSERT INTO conversations
                (session_id, title, created_at, updated_at, transcript, context, settings)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                title=excluded.title, updated_at=excluded.updated_at, transcript=excluded.transcript, settings=excluded.settings""",
                (session.session_id, session.title, session.created_at, time.time(),
                 json.dumps(transcript, ensure_ascii=False), json.dumps(context, ensure_ascii=False), json.dumps(settings, ensure_ascii=False)))

    def save_context(self, session: ChatSession) -> None:
        with closing(self._connect()) as conn, conn:
            # A late worker finishing after deletion must never recreate a chat.
            conn.execute("UPDATE conversations SET context=? WHERE session_id=?",
                         (json.dumps(self._context(session), ensure_ascii=False), session.session_id))

    def save_settings(self, session: ChatSession) -> None:
        settings = {key: value for key, value in self._context(session).items() if key != "messages"}
        with closing(self._connect()) as conn, conn:
            conn.execute("UPDATE conversations SET settings=? WHERE session_id=?",
                         (json.dumps(settings, ensure_ascii=False), session.session_id))

    def list_conversations(self) -> List[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT session_id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC")]

    def load(self, session_id: str) -> Optional[Dict[str, Any]]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM conversations WHERE session_id=?", (session_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["transcript"] = json.loads(result["transcript"])
        result["context"] = json.loads(result["context"])
        result["context"].update(json.loads(result.pop("settings")))
        self._recover_completed_turn(result)
        return result

    @staticmethod
    def _recover_completed_turn(record: Dict[str, Any]) -> None:
        """Recover a finished response whose GUI callbacks were pending at exit."""
        transcript = record["transcript"]
        messages = record["context"].get("messages", [])
        if not transcript or not transcript[-1].get("pending") or not messages:
            return
        if sum(row["role"] == "user" for row in transcript) != sum(row["role"] == "user" for row in messages):
            return
        if messages[-1]["role"] != "assistant" or messages[-1].get("tool_calls"):
            return
        last_user = max(index for index, row in enumerate(messages) if row["role"] == "user")
        turn = messages[last_user + 1:]
        text = "\n\n".join(row["content"] for row in turn
                             if row["role"] == "assistant" and isinstance(row.get("content"), str) and row["content"])
        final = transcript[-1]
        final.update(content=text or final.get("content", ""), pending=False)
        reasoning = "".join(row.get("reasoning") or "" for row in turn if row["role"] == "assistant")
        calls = [call for row in turn for call in row.get("tool_calls") or []]
        if not final.get("activity") and not calls and not reasoning:
            return
        activity = final.setdefault("activity", {})
        previous_tools = list(activity.get("tools", []))
        results = {row.get("tool_call_id"): row for row in turn if row["role"] == "tool"}
        tools = []
        for call in calls:
            name = call["function"]["name"]
            existing = next((tool for tool in previous_tools if tool.get("call_id") == call["id"] or
                             tool.get("tool_name") == name), None)
            if existing is not None:
                previous_tools.remove(existing)
            tool = dict(existing or {})
            try:
                arguments = json.loads(call["function"].get("arguments") or "{}")
            except (ValueError, TypeError):
                arguments = {}
            tool.update(tool_name=name, call_id=call["id"], arguments=arguments,
                        server_name=tool.get("server_name") or (name.split("_")[1] if name.startswith("mcp_") else "Sistem"))
            result = results.get(call["id"])
            if result is not None:
                if tool.get("status") != "error":
                    tool.update(status="done", output=result.get("content") or "")
            else:
                tool["status"] = "stopped"
            tools.append(tool)
        activity.update(tools=tools + previous_tools, reasoning=reasoning or activity.get("reasoning", ""),
                        running=False, cancelled=False)

    def restore_session(self, record: Dict[str, Any]) -> ChatSession:
        context = record["context"]
        session = ChatSession(session_id=record["session_id"], title=record["title"],
                              model=context.get("model", "glm-5.3"),
                              system_prompt=context.get("system_prompt", ""),
                              temperature=context.get("temperature", 0.7),
                              max_tokens=context.get("max_tokens", 2048),
                              reasoning_effort=context.get("reasoning_effort"),
                              active_mcp_servers=set(context.get("active_mcp_servers", [])),
                              enabled_tool_names=context.get("enabled_tool_names"))
        session.created_at = record["created_at"]
        session.enabled_tool_names = set(context["enabled_tool_names"]) if context.get("enabled_tool_names") is not None else None
        session.messages = [Message.model_validate(message) for message in context.get("messages", [])]
        # An interrupted run may leave unanswered tool calls. Close those before
        # sending the restored context to a provider, without re-executing tools.
        pending = {}
        repaired = []
        for message in session.messages:
            if message.role != "tool" and pending:
                repaired.extend(Message(role="tool", tool_call_id=call.id, name=call.function.name,
                                        content="İşlem kesildi; kaydedilmiş sonuç bulunmuyor.") for call in pending.values())
                pending.clear()
            repaired.append(message)
            if message.tool_calls:
                pending.update((call.id, call) for call in message.tool_calls)
            if message.role == "tool":
                pending.pop(message.tool_call_id, None)
        repaired.extend(Message(role="tool", tool_call_id=call.id, name=call.function.name,
                                content="İşlem kesildi; kaydedilmiş sonuç bulunmuyor.") for call in pending.values())
        session.messages = repaired
        # Prompt-only or offline UI records still provide usable chat context.
        user_count = sum(message.role == "user" for message in session.messages)
        display_user_count = 0
        for row in record["transcript"]:
            display_user_count += row["role"] == "user"
            if display_user_count > user_count and (row.get("content") or row["role"] == "user"):
                session.messages.append(Message(role=row["role"], content=row.get("content")))
        if session.messages and session.messages[-1].role in ("user", "tool"):
            # A response may be displayed before the runtime finishes saving it.
            final = record["transcript"][-1]
            if final.get("role") == "assistant" and final.get("content"):
                session.messages.append(Message(role="assistant", content=final.get("content")))
        return session

    def delete(self, session_id: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM conversations WHERE session_id=?", (session_id,))
