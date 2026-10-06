from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.processors import TransformationInput
from prompt_toolkit.output import DummyOutput

from firik_agent.paste import (
    create_paste_badge,
    delete_badge_on_backspace,
    delete_badge_on_delete,
    expand_pastes,
    PasteBadgeProcessor,
    PASTE_BADGE_PATTERN,
)
from firik_agent import cli
from firik_agent.core.agent import Agent


def test_create_paste_badge_short_text():
    short_text = "hello world this is short"
    badge, is_badge = create_paste_badge(short_text)
    assert not is_badge
    assert badge == short_text


def test_create_paste_badge_multiline():
    multiline = "line 1\nline 2\nline 3"
    badge, is_badge = create_paste_badge(multiline)
    assert is_badge
    assert badge == "[Pasted text: 6 words]"


def test_create_paste_badge_long_single_line():
    long_text = " ".join([f"word{i}" for i in range(25)])
    badge, is_badge = create_paste_badge(long_text)
    assert is_badge
    assert badge == "[Pasted text: 25 words]"


def test_create_paste_badge_single_word_multiline():
    text = "word\n"
    badge, is_badge = create_paste_badge(text)
    # Single line when stripped
    assert not is_badge
    assert badge == "word\n"

    two_lines = "foo\nbar"
    badge2, is_badge2 = create_paste_badge(two_lines)
    assert is_badge2
    assert badge2 == "[Pasted text: 2 words]"


def test_create_paste_badge_duplicate_numbering():
    existing: dict[str, str] = {}
    text1 = " ".join(["alpha"] * 25)
    badge1, is_badge1 = create_paste_badge(text1, existing)
    assert is_badge1
    assert badge1 == "[Pasted text: 25 words]"
    existing[badge1] = text1

    text2 = " ".join(["beta"] * 25)
    badge2, is_badge2 = create_paste_badge(text2, existing)
    assert is_badge2
    assert badge2 == "[Pasted text #2: 25 words]"
    existing[badge2] = text2

    text3 = " ".join(["gamma"] * 25)
    badge3, is_badge3 = create_paste_badge(text3, existing)
    assert is_badge3
    assert badge3 == "[Pasted text #3: 25 words]"


def test_expand_pastes():
    paste_map = {
        "[Pasted text: 25 words]": "alpha " * 25,
        "[Pasted text #2: 25 words]": "beta " * 25,
    }
    user_input = "Please check: [Pasted text: 25 words] and then [Pasted text #2: 25 words]"
    expanded = expand_pastes(user_input, paste_map)
    assert "[Pasted text:" not in expanded
    assert "alpha " * 25 in expanded
    assert "beta " * 25 in expanded

    # Tag not in map should remain untouched
    unrelated = "Here is [Pasted text: 99 words]"
    assert expand_pastes(unrelated, paste_map) == unrelated


def test_delete_badge_on_backspace():
    buf = Buffer()

    # Case 1: Cursor right after badge
    text = "hello [Pasted text: 25 words]"
    buf.set_document(Document(text, len(text)))
    deleted = delete_badge_on_backspace(buf)
    assert deleted
    assert buf.text == "hello "
    assert buf.cursor_position == 6

    # Case 2: Cursor inside badge
    buf.set_document(Document("hello [Pasted text: 25 words] world", 15))
    deleted = delete_badge_on_backspace(buf)
    assert deleted
    assert buf.text == "hello  world"
    assert buf.cursor_position == 6

    # Case 3: Cursor before badge
    buf.set_document(Document("hello [Pasted text: 25 words]", 3))
    deleted = delete_badge_on_backspace(buf)
    assert not deleted
    assert buf.text == "hello [Pasted text: 25 words]"


def test_delete_badge_on_delete():
    buf = Buffer()

    # Case 1: Cursor at start of badge
    buf.set_document(Document("hello [Pasted text: 25 words] world", 6))
    deleted = delete_badge_on_delete(buf)
    assert deleted
    assert buf.text == "hello  world"
    assert buf.cursor_position == 6

    # Case 2: Cursor inside badge
    buf.set_document(Document("hello [Pasted text: 25 words] world", 12))
    deleted = delete_badge_on_delete(buf)
    assert deleted
    assert buf.text == "hello  world"
    assert buf.cursor_position == 6

    # Case 3: Cursor at end of badge
    buf.set_document(Document("hello [Pasted text: 25 words] world", 29))
    deleted = delete_badge_on_delete(buf)
    assert not deleted


def test_paste_badge_processor():
    processor = PasteBadgeProcessor()
    text = "Query: [Pasted text: 25 words] end"
    ti = TransformationInput(
        buffer_control=None,
        document=Document(text, 0),
        lineno=0,
        source_to_display=lambda i: i,
        fragments=[("", text)],
        width=80,
        height=1,
    )
    result = processor.apply_transformation(ti)
    fragments = result.fragments
    assert len(fragments) == 3
    assert fragments[0] == ("", "Query: ")
    assert fragments[1] == ("bold ansicyan reverse", "[Pasted text: 25 words]")
    assert fragments[2] == ("", " end")
    assert "".join(f[1] for f in fragments) == text


@pytest.mark.asyncio
async def test_repl_bracketed_paste_and_expansion_integration(monkeypatch):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    agent = Agent(config={"agent": {}})
    monkeypatch.setattr(agent, "initialize", AsyncMock())
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    received_prompts: list[str] = []

    async def run(**callbacks):
        received_prompts.append(callbacks.get("prompt", ""))
        return "Agent response"

    monkeypatch.setattr(agent, "run", run)

    with create_pipe_input() as pipe:
        def make_session(**kwargs):
            kwargs["history"] = InMemoryHistory()
            return PromptSession(input=pipe, output=DummyOutput(), **kwargs)

        monkeypatch.setattr(cli, "PromptSession", make_session)
        task = asyncio.create_task(cli.run_repl(agent))

        try:
            await asyncio.sleep(0.05)
            # Send bracketed paste with 25 words
            pasted_content = " ".join([f"item{i}" for i in range(25)])
            pipe.send_text(f"prefix \x1b[200~{pasted_content}\x1b[201~\r")

            # Wait for agent to process prompt
            for _ in range(50):
                if received_prompts:
                    break
                await asyncio.sleep(0.02)

            assert len(received_prompts) == 1
            assert "prefix " in received_prompts[0]
            assert pasted_content in received_prompts[0]
            assert "[Pasted text:" not in received_prompts[0]

            # Exit REPL
            pipe.send_text("/exit\r")
            await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
