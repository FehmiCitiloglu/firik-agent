"""Exact, review-only voice command recognition. No execution or approval API."""
from __future__ import annotations

from enum import Enum
import unicodedata
import re


class VoiceCommand(str, Enum):
    STOP_RESPONSE = "stop_response"
    NEW_CHAT = "new_chat"
    READ_LAST = "read_last"
    STOP_SPEECH = "stop_speech"
    SWITCH_CODING = "switch_coding"
    OPEN_TAB = "open_tab"
    SELECT_CONVERSATION = "select_conversation"
    SELECT_PROJECT = "select_project"
    VIEW_ACTIVITY = "view_activity"
    PAUSE_AGENT = "pause_agent"
    RESUME_AGENT = "resume_agent"
    MUTE_SPEECH = "mute_speech"
    UNMUTE_SPEECH = "unmute_speech"
    REPLAY_SPEECH = "replay_speech"
    DESCRIBE_PROJECT = "describe_project"
    AGENT_STATUS = "agent_status"


def strip_invocation(text: str) -> str:
    """Remove an explicit spoken name, without pretending it is a wake detector."""
    if not isinstance(text, str):
        return ""
    return re.sub(r"^\s*firik(?:\s*[,.:!?]\s*|\s+)", "", text, count=1, flags=re.IGNORECASE).strip()


def _normalize(text: str) -> str:
    # Casefold alone leaves the combining dot in Turkish capital İ.
    value = unicodedata.normalize("NFC", text).casefold().replace("i\u0307", "i").replace("ı", "i")
    return " ".join(value.strip().rstrip(".!?。！？").split())


_ALIASES = {
    VoiceCommand.STOP_RESPONSE: ("yanıtı durdur", "cevabı durdur", "yanıtı iptal et", "stop response", "cancel response", "stop agent", "cancel agent", "cancel task", "ajanı durdur", "ajanı iptal et", "görevi iptal et"),
    VoiceCommand.NEW_CHAT: ("yeni sohbet", "yeni sohbet aç", "new chat", "start new chat"),
    VoiceCommand.READ_LAST: ("son yanıtı oku", "son cevabı oku", "yanıtı oku", "read last response", "read response", "read your last answer aloud", "read your last response aloud"),
    VoiceCommand.STOP_SPEECH: ("seslendirmeyi durdur", "konuşmayı durdur", "okumayı durdur", "sesi durdur", "stop speaking", "stop speech"),
    VoiceCommand.SWITCH_CODING: ("switch to the existing coding conversation", "switch to coding conversation", "mevcut kodlama sohbetine geç", "kodlama sohbetine geç"),
    VoiceCommand.VIEW_ACTIVITY: ("view activity", "show activity", "open activity", "etkinliği göster", "etkinlikleri göster", "etkinliği aç"),
    VoiceCommand.PAUSE_AGENT: ("pause agent", "pause task", "ajanı duraklat", "görevi duraklat"),
    VoiceCommand.RESUME_AGENT: ("resume agent", "resume task", "ajanı devam ettir", "görevi devam ettir", "göreve devam et"),
    VoiceCommand.MUTE_SPEECH: ("mute speech", "mute voice", "sesi kapat", "seslendirmeyi kapat"),
    VoiceCommand.UNMUTE_SPEECH: ("unmute speech", "unmute voice", "sesi aç", "seslendirmeyi aç"),
    VoiceCommand.REPLAY_SPEECH: ("replay speech", "replay last response", "seslendirmeyi tekrar oynat", "son yanıtı tekrar oku"),
    VoiceCommand.DESCRIBE_PROJECT: ("describe the active project", "describe active project", "describe the current project", "describe current project", "etkin projeyi anlat", "aktif projeyi anlat", "mevcut projeyi anlat"),
    VoiceCommand.AGENT_STATUS: ("which agent is working", "agent status", "which agents are working", "çalışan ajanlar", "hangi ajan çalışıyor", "ajan durumu"),
}
_COMMANDS = {_normalize(alias): command for command, aliases in _ALIASES.items() for alias in aliases}

_TAB_NAMES = {
    "projects": ("projects", "projeler"), "chat": ("chat", "sohbet"),
    "mcp": ("mcp",), "ocr": ("ocr", "görsel ve ocr"),
    "transcribe": ("transcription", "ses çözümleme"),
    "rerank": ("rerank", "sıralama"), "quota": ("quota", "kota"),
    "settings": ("settings", "ayarlar"), "about": ("about", "hakkında"),
}
_TAB_COMMANDS = {}
for _tab, _names in _TAB_NAMES.items():
    for _name in _names:
        for _phrase in (f"open {_name}", f"open {_name} tab", f"{_name} aç", f"{_name} sekmesini aç"):
            _TAB_COMMANDS[_normalize(_phrase)] = _tab


def command_tab(text: str) -> str | None:
    """Return an existing built-in tab ID for a complete local command."""
    return _TAB_COMMANDS.get(_normalize(strip_invocation(text))) if isinstance(text, str) else None


def selection_command(text: str):
    """Parse an exact navigation grammar; inventory resolution remains separate.

    Returns (command, selector, explicit_id). A selector never creates an item.
    """
    if not isinstance(text, str) or len(text) > 500:
        return None
    value = strip_invocation(text).strip().rstrip(".!?。！？")
    patterns = (
        (VoiceCommand.SELECT_CONVERSATION, r"(?:open|select|switch to) (?:chat|conversation) (.+)"),
        (VoiceCommand.SELECT_PROJECT, r"(?:open|select|switch to) project (.+)"),
        (VoiceCommand.SELECT_CONVERSATION, r"(?:sohbet|konuşma) (.+?) (?:aç|seç)"),
        (VoiceCommand.SELECT_PROJECT, r"proje (.+?) (?:aç|seç)"),
        (VoiceCommand.SELECT_PROJECT, r"(.+?) projesini (?:aç|seç)"),
        (VoiceCommand.SELECT_CONVERSATION, r"(.+?) sohbetini (?:aç|seç)"),
    )
    for command, pattern in patterns:
        match = re.fullmatch(pattern, value, flags=re.IGNORECASE)
        if match:
            selector = match.group(1).strip()
            explicit = selector.casefold().startswith("id ")
            if explicit:
                selector = selector[3:].strip()
            if len(selector) >= 2 and selector[0] == selector[-1] and selector[0] in ("'", '"'):
                selector = selector[1:-1]
            if selector and len(selector) <= 200:
                return command, selector, explicit
    return None


def parse_command(text: str) -> VoiceCommand | None:
    """Suggest an allowlisted operation; the UI must show and confirm it.

    Arbitrary transcripts, compound instructions and tool-approval phrases stay
    ordinary text. This function deliberately cannot authorize agent tools.
    """
    if not isinstance(text, str) or len(text) > 200:
        return None
    value = _normalize(strip_invocation(text))
    direct = _COMMANDS.get(value) or (VoiceCommand.OPEN_TAB if value in _TAB_COMMANDS else None)
    selected = selection_command(text) if direct is None else None
    return direct or (selected[0] if selected else None)


def requires_review(text: str) -> bool:
    """Conservative auto-send policy; unknown/action-bearing requests need review.

    This is an input convenience, never a replacement for existing tool approval.
    Only ordinary explanatory requests are eligible; command execution has its
    own exact allowlist and confirmation path in the panel.
    """
    value = _normalize(strip_invocation(text))
    if not value or len(value) > 4000:
        return True
    command = parse_command(value)
    if command is not None:
        return command not in {VoiceCommand.STOP_SPEECH, VoiceCommand.READ_LAST}
    sensitive = (
        r"\b(delete|remove|erase|wipe|deploy|publish|send|email|message|purchase|buy|pay|"
        r"transfer|approve|allow|grant|revoke|execute|run|install|uninstall|commit|push|"
        r"merge|password|secret|token|credential|api.?key|sil|silin|silme|yay[ıi]nla|"
        r"gönder|gonder|sat[ıi]n|öde|ode|onayla|izin|çalıştır|calistir|yükle|yukle|"
        r"parola|şifre|sifre|anahtar)\b"
    )
    if re.search(sensitive, value):
        return True
    # Turkish verbs often occur at the end of the utterance.
    ordinary = re.search(
        r"^(explain|summarize|describe|translate|what|why|how|show|tell|compare|list)\b|"
        r"\b(aç[ıi]kla|acikla|özetle|ozetle|anlat|çevir|cevir|nedir|neden|nas[ıi]l)\b", value)
    return ordinary is None
