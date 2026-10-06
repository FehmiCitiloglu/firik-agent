"""Compact, opt-in inspection of a desktop chat turn's activity."""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from firik_agent.core.events import AgentEvent, AgentEventType
from firik_agent.mcp.models import redact_secrets
from firik_agent.ui.components.live_activity import ActivityIndicator, command_preview


def _set_text(widget: ctk.CTkTextbox, content: str) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", redact_secrets(content))
    widget.configure(state="disabled")


class _ReasoningTextbox(ctk.CTkTextbox):
    """Append streamed text and keep native scrolling inside this disclosure."""

    def __init__(self, master: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self._rendered_content = ""
        # Let Tk's Text class handle the wheel, then stop propagation before
        # CTkScrollableFrame's global binding also scrolls the conversation.
        tags = list(self._textbox.bindtags())
        tags.insert(tags.index("Text") + 1, "FirikReasoningScrollBoundary")
        self._textbox.bindtags(tuple(tags))
        for event in ("<MouseWheel>", "<Shift-MouseWheel>", "<Button-4>", "<Button-5>"):
            self.tk.call("bind", "FirikReasoningScrollBoundary", event, "break")

    def set_stream_text(self, content: str) -> None:
        content = redact_secrets(content)
        self.configure(state="normal")
        if content.startswith(self._rendered_content):
            self.insert("end", content[len(self._rendered_content):])
        else:
            # Redaction can change an earlier suffix once a secret completes.
            self.delete("1.0", "end")
            self.insert("1.0", content)
        self._rendered_content = content
        self.configure(state="disabled")
        self.see("end")


class ToolActivityBubble(ctk.CTkFrame):
    """A collapsed invocation with selectable, complete arguments and output."""

    def __init__(self, master: Any, tool_name: str, server_name: Optional[str] = None,
                 arguments: Optional[Dict[str, Any]] = None, **kwargs: Any) -> None:
        super().__init__(master, fg_color=("gray92", "#131d2e"), corner_radius=8,
                         border_width=1, border_color=("gray80", "#243247"), **kwargs)
        self.tool_name = tool_name
        parts = tool_name.split("_")
        self.server_name = server_name or (parts[1] if tool_name.startswith("mcp_") and len(parts) > 1 else "Sistem")
        self.arguments = arguments or {}
        self.output_content = ""
        self.error_content = ""
        self.duration_ms: Optional[float] = None
        self.status = "running"
        self.is_expanded = False
        self.call_id: Optional[str] = None
        self.details_frame: Optional[ctk.CTkFrame] = None

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=10, pady=6)
        header.grid_columnconfigure(0, weight=1)
        display_tool = tool_name.removeprefix(f"mcp_{self.server_name}_")
        self.title_label = ctk.CTkLabel(header, text=f"{self.server_name} · {display_tool}",
                                      width=1, anchor="w", justify="left", wraplength=400,
                                      font=ctk.CTkFont(size=12, weight="bold"),
                                      text_color=("#0369a1", "#38bdf8"))
        self.title_label.grid(row=0, column=0, sticky="ew")
        self.command_label = ctk.CTkLabel(header, text=command_preview(tool_name, self.arguments),
                                          width=1, anchor="w", justify="left", wraplength=400,
                                          font=ctk.CTkFont(family="monospace", size=11),
                                          text_color=("#334155", "#cbd5e1"))
        self.command_label.grid(row=1, column=0, sticky="ew")
        self.status_badge = ActivityIndicator(header,
                                        font=ctk.CTkFont(size=11), text_color=("#92400e", "#fbbf24"))
        self.status_badge.grid(row=2, column=0, sticky="ew")
        self.status_badge.start("Çalışıyor")
        self.toggle_btn = ctk.CTkButton(header, text="Detay ▸", width=64, height=26,
                                       font=ctk.CTkFont(size=11), fg_color="transparent",
                                       hover_color=("gray80", "#1e293b"),
                                       text_color=("gray30", "#cbd5e1"), command=self._toggle_expand)
        self.toggle_btn.grid(row=0, column=1, rowspan=3, padx=(6, 0))
        def resize(event):
            width = max(40, int(event.width / self._get_widget_scaling()) - 80)
            for label in (self.title_label, self.command_label, self.status_badge):
                label.configure(wraplength=width)
        header.bind("<Configure>", resize, add="+")

    def _build_details(self) -> None:
        self.details_frame = ctk.CTkFrame(self, fg_color=("gray88", "#0b1320"), corner_radius=6)
        ctk.CTkLabel(self.details_frame, text="Parametreler / Komut", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(fill="x", padx=8, pady=(6, 2))
        self.args_text = ctk.CTkTextbox(self.details_frame, height=120, wrap="word",
                                       font=ctk.CTkFont(family="monospace", size=11))
        self.args_text.pack(fill="x", padx=8, pady=(0, 6))
        ctk.CTkLabel(self.details_frame, text="Çıktı", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(fill="x", padx=8)
        self.output_text = ctk.CTkTextbox(self.details_frame, height=180, wrap="word",
                                         font=ctk.CTkFont(family="monospace", size=11))
        self.output_text.pack(fill="x", padx=8, pady=(2, 6))
        ctk.CTkButton(self.details_frame, text="Çıktıyı kopyala", width=110, height=24,
                      font=ctk.CTkFont(size=11), command=self._copy_output).pack(anchor="e", padx=8, pady=(0, 8))
        self._refresh_details()

    def _refresh_details(self) -> None:
        if self.details_frame is not None:
            _set_text(self.args_text, json.dumps(self.arguments, indent=2, ensure_ascii=False))
            _set_text(self.output_text, self.error_content or self.output_content or
                      ("Sonuç bekleniyor…" if self.status == "running" else "Çıktı yok."))

    def _copy_output(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(redact_secrets(self.error_content or self.output_content))

    def _toggle_expand(self) -> None:
        if self.is_expanded:
            self.details_frame.pack_forget()
        else:
            if self.details_frame is None:
                self._build_details()
            self.details_frame.pack(fill="x", padx=8, pady=(0, 6))
        self.is_expanded = not self.is_expanded
        self.toggle_btn.configure(text="Detay ▾" if self.is_expanded else "Detay ▸")

    def set_arguments(self, arguments: Dict[str, Any]) -> None:
        self.arguments.update(arguments)
        self.command_label.configure(text=command_preview(self.tool_name, self.arguments))
        self.status_badge.set_stage("Çalışıyor")
        self._refresh_details()

    def set_result(self, content: str, duration_ms: Optional[float] = None) -> None:
        self.output_content = content
        self.duration_ms = duration_ms
        self.status = "done"
        self.status_badge.stop()
        duration = f" · {duration_ms:.0f} ms" if duration_ms is not None else ""
        self.status_badge.configure(text=f"✓ Tamamlandı{duration}", text_color=("#047857", "#34d399"))
        self._refresh_details()

    def set_error(self, error_msg: str, duration_ms: Optional[float] = None) -> None:
        self.error_content = error_msg
        self.duration_ms = duration_ms
        self.status = "error"
        self.status_badge.stop()
        duration = f" · {duration_ms:.0f} ms" if duration_ms is not None else ""
        self.status_badge.configure(text=f"⚠ Hata{duration}", text_color=("#b91c1c", "#f87171"))
        self._refresh_details()

    def set_stopped(self) -> None:
        self.status = "stopped"
        self.status_badge.stop()
        self.status_badge.configure(text="Durduruldu", text_color=("gray40", "#94a3b8"))
        self._refresh_details()


class ToolActivityGroup(ctk.CTkFrame):
    """One closed disclosure per assistant turn, including all repeated calls."""

    def __init__(self, master: Any, on_collapse: Optional[Callable[[], None]] = None,
                 **kwargs: Any) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.tools: List[ToolActivityBubble] = []
        self.on_collapse = on_collapse
        self.is_expanded = False
        self.running = True
        self.cancelled = False
        self.failed = False
        self.phase = "Model yanıtı bekleniyor"
        self._reasoning_parts: List[str] = []
        self.reasoning_text: Optional[_ReasoningTextbox] = None
        self.grid_columnconfigure(0, weight=1)
        self.toggle_btn = ctk.CTkButton(self, text="İşlem ayrıntıları ▸", height=30, anchor="w",
                                       fg_color=("#e2e8f0", "#172336"),
                                       hover_color=("#cbd5e1", "#243247"),
                                       text_color=("#334155", "#cbd5e1"),
                                       font=ctk.CTkFont(size=12), command=self._toggle_expand)
        self.toggle_btn.grid(row=0, column=0, sticky="ew")
        self.details_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.reasoning_btn = ctk.CTkButton(self.details_frame, text="Model düşüncesi ▸", anchor="w",
                                          height=26, fg_color="transparent",
                                          text_color=("gray30", "#94a3b8"),
                                          command=self._toggle_reasoning)
        self._reasoning_expanded = False

    def _toggle_expand(self) -> None:
        if self.is_expanded:
            self.details_frame.grid_remove()
        else:
            self.details_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.is_expanded = not self.is_expanded
        self._refresh_summary()
        if not self.is_expanded and self.on_collapse:
            self.after_idle(self.on_collapse)

    def _toggle_reasoning(self) -> None:
        if self._reasoning_expanded:
            self.reasoning_text.pack_forget()
        else:
            if self.reasoning_text is None:
                self.reasoning_text = _ReasoningTextbox(self.details_frame, height=180, wrap="word",
                                                     font=ctk.CTkFont(size=12))
            self.reasoning_text.pack(fill="x", pady=4, after=self.reasoning_btn)
            self.reasoning_text.set_stream_text("".join(self._reasoning_parts))
        self._reasoning_expanded = not self._reasoning_expanded
        self.reasoning_btn.configure(text="Model düşüncesi ▾" if self._reasoning_expanded else "Model düşüncesi ▸")

    def append_reasoning(self, content: str) -> None:
        if not self._reasoning_parts:
            self.reasoning_btn.pack(fill="x", pady=4)
        self._reasoning_parts.append(content)
        if self._reasoning_expanded:
            self.reasoning_text.set_stream_text("".join(self._reasoning_parts))
        self._refresh_summary()

    @staticmethod
    def _call_id(event: AgentEvent) -> Optional[str]:
        return event.metadata.get("tool_call_id") or event.metadata.get("call_id")

    def start_tool(self, event: AgentEvent) -> ToolActivityBubble:
        tool = ToolActivityBubble(self.details_frame, tool_name=event.tool_name or "tool",
                                  server_name=event.server_name, arguments=event.arguments)
        tool.call_id = self._call_id(event)
        tool.pack(fill="x", pady=3)
        self.tools.append(tool)
        self._refresh_summary()
        return tool

    def update_tool(self, event: AgentEvent) -> None:
        call_id = self._call_id(event)
        tool = next((tool for tool in self.tools if tool.status == "running" and
                     (tool.call_id == call_id if call_id else
                      tool.tool_name == (event.tool_name or "tool") and
                      (not event.server_name or tool.server_name == event.server_name))), None)
        if tool is None:
            tool = self.start_tool(event)
        if event.type == AgentEventType.TOOL_CALL_ARGUMENTS:
            tool.set_arguments(event.arguments or {})
        elif event.type == AgentEventType.TOOL_CALL_ERROR:
            tool.set_error(event.content or "Bilinmeyen hata", event.duration_ms)
        else:
            tool.set_result(event.content or "", event.duration_ms)
        self._refresh_summary()

    def add_connection_error(self, event: AgentEvent) -> None:
        tool = ToolActivityBubble(self.details_frame, tool_name="MCP bağlantısı", server_name=event.server_name)
        tool.pack(fill="x", pady=3)
        tool.set_error(event.content or "Bağlantı kurulamadı")
        self.tools.append(tool)
        self._refresh_summary()

    def finish(self, *, cancelled: bool = False, failed: bool = False) -> None:
        self.running = False
        self.cancelled = cancelled
        self.failed = failed
        for tool in self.tools:
            if tool.status == "running":
                tool.set_stopped()
        self._refresh_summary()

    def to_record(self) -> Dict[str, Any]:
        return {
            "reasoning": "".join(self._reasoning_parts), "running": self.running,
            "cancelled": self.cancelled, "failed": self.failed,
            "tools": [{"tool_name": tool.tool_name, "server_name": tool.server_name,
                       "arguments": dict(tool.arguments), "status": tool.status,
                       "output": tool.output_content, "error": tool.error_content,
                       "duration_ms": tool.duration_ms, "call_id": tool.call_id} for tool in self.tools],
        }

    @classmethod
    def from_record(cls, master: Any, record: Dict[str, Any]) -> "ToolActivityGroup":
        group = cls(master)
        if record.get("reasoning"):
            group.append_reasoning(record["reasoning"])
        for item in record.get("tools", []):
            tool = group.start_tool(AgentEvent(
                AgentEventType.TOOL_CALL_STARTED, session_id="history",
                tool_name=item["tool_name"], server_name=item.get("server_name"),
                arguments=item.get("arguments"), metadata={"call_id": item.get("call_id")}))
            if item["status"] == "done":
                tool.set_result(item.get("output", ""), item.get("duration_ms"))
            elif item["status"] == "error":
                tool.set_error(item.get("error", ""), item.get("duration_ms"))
            else:
                tool.set_stopped()
        group.finish(cancelled=record.get("cancelled", False) or record.get("running", False),
                     failed=record.get("failed", False))
        return group

    def _refresh_summary(self) -> None:
        errors = sum(tool.status == "error" for tool in self.tools)
        active = [tool for tool in self.tools if tool.status == "running"]
        state = (f"{len(active)} komut çalışıyor" if active else self.phase) if self.running else (
            "Durduruldu" if self.cancelled else "Hata" if self.failed else "Tamamlandı")
        count = f" · {len(self.tools)} işlem" if self.tools else ""
        warning = f" · {errors} hata" if errors else ""
        arrow = "▾" if self.is_expanded else "▸"
        self.toggle_btn.configure(text=f"İşlem ayrıntıları{count} · {state}{warning} {arrow}",
                                  text_color=("#b91c1c", "#f87171") if errors or self.failed else ("#334155", "#cbd5e1"))
