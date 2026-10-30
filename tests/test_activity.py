from __future__ import annotations

import asyncio
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.mouse_events import MouseEventType
from rich.console import Console

from firik_agent.activity import ActivityLog, ActivityView
from firik_agent import cli
from firik_agent.core.agent import Agent
from firik_agent.core.events import AgentEvent, AgentEventType


def visible(view):
    return ''.join(fragment[1] for fragment in view.fragments())


async def wait_for(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(poll(), timeout=2)


def test_folded_details_preserve_complete_literal_output_and_repeated_calls():
    log = ActivityLog()
    log.thought('private reasoning')
    output = '[bold]literal[/bold]\n' + 'result\n' * 1000 + 'END'
    for _ in range(2):
        log.tool_start('fetch', {'url': 'https://example.test'})
        log.tool_end('fetch', output)
    view = ActivityView(log, output=DummyOutput())
    assert 'private reasoning' not in visible(view)
    assert 'END' not in visible(view)
    assert '2 tool calls' in visible(view)
    view.cursor = 3
    view.toggle_selected()
    assert not log.entries[1].expanded
    assert log.entries[2].expanded
    assert output in log.entries[2].detail
    assert '[bold]literal[/bold]' in visible(view)
    assert 'END' in visible(view)
    view.cursor = len(view._owners) - 1
    view.toggle_selected()
    assert view.cursor == 3
    assert 'END' not in visible(view)


@pytest.mark.asyncio
async def test_live_keyboard_toggle_and_worker_completion():
    log = ActivityLog()
    log.running = True
    done = asyncio.Event()

    async def operation():
        log.tool_start('fetch', {'x': 1})
        await done.wait()
        log.tool_end('fetch', 'full output')
        return 'answer'

    with create_pipe_input() as pipe:
        view = ActivityView(log, live=True, input=pipe, output=DummyOutput())
        task = asyncio.create_task(view.run(operation))
        await wait_for(lambda: bool(log.entries))
        assert not view.expanded
        pipe.send_text('\x0f')  # Ctrl+O
        await wait_for(lambda: view.expanded)
        pipe.send_text('\x1b[B\r')  # Down, Enter
        await wait_for(lambda: log.entries[0].expanded)
        pipe.send_text('\x0f')
        await wait_for(lambda: not view.expanded)
        done.set()
        assert await asyncio.wait_for(task, 2) == 'answer'
        assert log.entries[0].status == 'returned'


@pytest.mark.asyncio
async def test_inspector_mouse_and_shortcut():
    log = ActivityLog()
    log.tool_start('fetch', {})
    log.tool_end('fetch', 'last line')
    with create_pipe_input() as pipe:
        view = ActivityView(log, input=pipe, output=DummyOutput())
        task = asyncio.create_task(view.run())
        await wait_for(lambda: view.app.is_running)
        row = view.fragments()[1]
        row[2](SimpleNamespace(event_type=MouseEventType.MOUSE_UP))
        assert log.entries[0].expanded
        assert 'last line' in visible(view)
        view.fragments()[1][2](SimpleNamespace(event_type=MouseEventType.MOUSE_UP))
        assert not log.entries[0].expanded
        pipe.send_text('\x0f')
        await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_cancelling_view_cleans_up_worker():
    log = ActivityLog()
    started, stopped = asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    with create_pipe_input() as pipe:
        view = ActivityView(log, live=True, input=pipe, output=DummyOutput())
        task = asyncio.create_task(view.run(operation))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        assert not view.app.is_running


@pytest.mark.asyncio
async def test_repl_compact_fallback_details_and_clear(monkeypatch):
    agent = Agent(config={'agent': {}})
    monkeypatch.setattr(agent, 'initialize', AsyncMock())
    output = Console(file=StringIO(), width=120)
    monkeypatch.setattr(cli, 'console', output)
    inputs = iter(['hello', '/details', '/clear', '/details', '/exit'])
    before_details = []

    class Session:
        async def prompt_async(self, *args):
            before_details.append(output.file.getvalue())
            return next(inputs)

    async def run(**callbacks):
        callbacks['on_thought']('REASONING DETAIL')
        callbacks['on_tool_start']('fetch', {'argument': 'ARGUMENT DETAIL'})
        callbacks['on_tool_end']('fetch', 'OUTPUT DETAIL [bold]literal[/bold]')
        return 'Final answer'

    monkeypatch.setattr(cli, 'PromptSession', lambda **kwargs: Session())
    monkeypatch.setattr(agent, 'run', run)
    await cli.run_repl(agent)
    assert 'Final answer' in before_details[1]
    for detail in ['REASONING DETAIL', 'ARGUMENT DETAIL', 'OUTPUT DETAIL']:
        assert detail not in before_details[1]
        assert detail in before_details[2]
    assert '[bold]literal[/bold]' in before_details[2]
    assert 'No activity yet.' in before_details[4]


@pytest.mark.asyncio
async def test_repl_recovery_is_visible_as_retry_not_model_thinking(monkeypatch):
    agent = Agent(config={'agent': {}})
    monkeypatch.setattr(agent, 'initialize', AsyncMock())
    output = Console(file=StringIO(), width=140)
    monkeypatch.setattr(cli, 'console', output)
    inputs = iter(['hello', '/details', '/exit'])

    class Session:
        async def prompt_async(self, *args):
            return next(inputs)

    async def run(**callbacks):
        for _ in range(2):
            callbacks['on_event'](AgentEvent(AgentEventType.RECOVERY, 'test',
                content='Provider is busy.', metadata={
                    'retry_eligible': True, 'delay': 2.0, 'failure_kind': 'provider_overloaded'}))
        callbacks['on_thought']('ACTUAL MODEL REASONING')
        return 'Final answer'

    monkeypatch.setattr(cli, 'PromptSession', lambda **kwargs: Session())
    monkeypatch.setattr(agent, 'run', run)
    await cli.run_repl(agent)
    rendered = output.file.getvalue()
    assert '2 retries' in rendered
    assert '1 thinking blocks' in rendered
    assert '3 thinking blocks' not in rendered
    assert 'Retrying in 2.0s.' in rendered
    assert 'Provider is busy.' in rendered


@pytest.mark.asyncio
async def test_prompt_inspector_preserves_draft(monkeypatch):
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    agent = Agent(config={'agent': {}})
    monkeypatch.setattr(agent, 'initialize', AsyncMock())
    output = Console(file=StringIO(), force_terminal=True, width=120)
    monkeypatch.setattr(cli, 'console', output)
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    sessions, views = [], []

    async def run(**callbacks):
        callbacks['on_tool_start']('fetch', {})
        callbacks['on_tool_end']('fetch', 'complete result')
        return 'Final answer'

    monkeypatch.setattr(agent, 'run', run)
    with create_pipe_input() as pipe:
        def make_session(**kwargs):
            kwargs['history'] = InMemoryHistory()
            session = PromptSession(input=pipe, output=DummyOutput(), **kwargs)
            sessions.append(session)
            return session

        def make_view(*args, **kwargs):
            view = ActivityView(*args, input=pipe, output=DummyOutput(), **kwargs)
            views.append(view)
            return view

        monkeypatch.setattr(cli, 'PromptSession', make_session)
        monkeypatch.setattr(cli, 'ActivityView', make_view)
        task = asyncio.create_task(cli.run_repl(agent))
        try:
            await wait_for(lambda: sessions and sessions[0].app.is_running)
            pipe.send_text('hello\r')
            await wait_for(lambda: 'Final answer' in output.file.getvalue())
            await wait_for(lambda: sessions[0].app.is_running)
            pipe.send_text('unsent draft\x0f')
            await wait_for(lambda: len(views) == 2 and views[1].app.is_running)
            pipe.send_text('\x0f')
            await wait_for(lambda: not views[1].app.is_running)
            assert sessions[0].default_buffer.text == 'unsent draft'
            pipe.send_text('\x15/exit\r')  # clear draft, exit REPL
            await asyncio.wait_for(task, 2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


def test_mouse_scroll_reaches_long_single_line_and_resize():
    from prompt_toolkit.data_structures import Size

    class Output(DummyOutput):
        columns = 40

        def get_size(self):
            return Size(rows=12, columns=self.columns)

    log = ActivityLog()
    log.tool_start('fetch', {})
    log.tool_end('fetch', 'x' * 3000 + 'TAIL')
    log.entries[0].expanded = True
    output = Output()
    view = ActivityView(log, output=output)
    rows = view.fragments()
    assert len(rows) > 80
    for _ in range(100):
        rows[0][2](SimpleNamespace(event_type=MouseEventType.SCROLL_DOWN))
    assert view.cursor == len(rows) - 1
    assert 'TAIL' in rows[view.cursor][1]
    output.columns = 100
    resized = view.fragments()
    assert len(resized) < len(rows)
    assert view.cursor == len(resized) - 1
    assert 'TAIL' in resized[view.cursor][1]


@pytest.mark.asyncio
async def test_activity_vim_navigation_shortcuts():
    log = ActivityLog()
    for i in range(15):
        log.thought(f"Thought {i}")
    with create_pipe_input() as pipe:
        view = ActivityView(log, input=pipe, output=DummyOutput())
        task = asyncio.create_task(view.run())
        await wait_for(lambda: view.app.is_running)
        assert view.cursor == 0

        # j moves down
        pipe.send_text("j")
        await wait_for(lambda: view.cursor == 1)

        # k moves up
        pipe.send_text("k")
        await wait_for(lambda: view.cursor == 0)

        # G moves to bottom
        pipe.send_text("G")
        last = len(view._owners) - 1
        await wait_for(lambda: view.cursor == last)

        # gg moves to top
        pipe.send_text("gg")
        await wait_for(lambda: view.cursor == 0)

        # d moves down 10
        pipe.send_text("d")
        await wait_for(lambda: view.cursor == 10)

        # u moves up 10
        pipe.send_text("u")
        await wait_for(lambda: view.cursor == 0)

        # o toggles expansion
        pipe.send_text("j")
        await wait_for(lambda: view.cursor == 1)
        pipe.send_text("o")
        await wait_for(lambda: log.entries[0].expanded)
        pipe.send_text("o")
        await wait_for(lambda: not log.entries[0].expanded)

        # q exits
        pipe.send_text("q")
        await asyncio.wait_for(task, 2)
        assert not view.app.is_running

