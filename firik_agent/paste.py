"""Compact badge handling for pasted text and long prompts in the terminal REPL.

Collapses multi-line or long pastes into concise badges (e.g. [Pasted text: 45 words]),
maintaining a clean terminal view while providing the agent with the full original text.
Badges can be deleted atomically with a single backspace or delete keypress.
"""
from __future__ import annotations

import re
from typing import Any

try:
    from prompt_toolkit.layout.processors import Processor, Transformation, TransformationInput
    HAVE_PROMPT_TOOLKIT = True
except ImportError:
    HAVE_PROMPT_TOOLKIT = False
    Processor = object  # type: ignore[misc,assignment]


PASTE_BADGE_PATTERN = re.compile(r"\[Pasted text(?: #\d+)?: \d+ words?\]")
PASTE_BADGE_CAPTURING = re.compile(r"(\[Pasted text(?: #\d+)?: \d+ words?\])")


def create_paste_badge(
    text: str,
    existing_tags: set[str] | dict[str, str] | None = None,
) -> tuple[str, bool]:
    """Inspect pasted text and return a concise badge if long/multiline, else the text itself.

    Returns:
        (result_text, was_converted_to_badge)
    """
    data = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = data.strip()
    if not cleaned:
        return data, False

    words = len(cleaned.split())
    lines = len(cleaned.splitlines())

    # Trigger threshold: 20+ words or multi-line text
    if words < 20 and lines <= 1:
        return data, False

    word_label = "word" if words == 1 else "words"
    existing = (
        set(existing_tags.keys())
        if isinstance(existing_tags, dict)
        else (set(existing_tags) if existing_tags else set())
    )

    base_tag = f"[Pasted text: {words} {word_label}]"
    if base_tag not in existing:
        return base_tag, True

    idx = 2
    while f"[Pasted text #{idx}: {words} {word_label}]" in existing:
        idx += 1
    return f"[Pasted text #{idx}: {words} {word_label}]", True


def expand_pastes(text: str, paste_map: dict[str, str]) -> str:
    """Expand any paste badges in text back to their full pasted contents."""
    if not text or not paste_map:
        return text

    # Match existing badges in text, sorted by length descending to avoid prefix clashes
    matching_tags = [
        re.escape(k) for k in sorted(paste_map.keys(), key=len, reverse=True) if k in text
    ]
    if not matching_tags:
        return text

    pattern = re.compile("|".join(matching_tags))
    return pattern.sub(lambda m: paste_map[m.group(0)], text)


def delete_badge_on_backspace(buf: Any) -> bool:
    """If cursor is inside or right after a paste badge, delete the entire badge atomically."""
    pos = buf.cursor_position
    text = buf.text
    for m in PASTE_BADGE_PATTERN.finditer(text):
        if m.start() < pos <= m.end():
            start, end = m.start(), m.end()
            buf.text = text[:start] + text[end:]
            buf.cursor_position = start
            return True
    return False


def delete_badge_on_delete(buf: Any) -> bool:
    """If cursor is at start or inside a paste badge, delete the entire badge atomically."""
    pos = buf.cursor_position
    text = buf.text
    for m in PASTE_BADGE_PATTERN.finditer(text):
        if m.start() <= pos < m.end():
            start, end = m.start(), m.end()
            buf.text = text[:start] + text[end:]
            buf.cursor_position = start
            return True
    return False


if HAVE_PROMPT_TOOLKIT:
    class PasteBadgeProcessor(Processor):
        """Highlights paste badges with a distinct pill/badge style in prompt_toolkit."""

        def apply_transformation(self, ti: TransformationInput) -> Transformation:
            fragments = []
            for style, text in ti.fragments:
                for part in PASTE_BADGE_CAPTURING.split(text):
                    if PASTE_BADGE_PATTERN.fullmatch(part):
                        fragments.append(("bold ansicyan reverse", part))
                    elif part:
                        fragments.append((style, part))
            return Transformation(fragments or [("", "")])
else:
    class PasteBadgeProcessor:  # type: ignore[no-redef]
        """Fallback dummy processor when prompt_toolkit is not installed."""
        pass
