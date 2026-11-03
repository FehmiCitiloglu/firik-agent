"""Streaming speech is bound to controller response and interruption lifetimes."""
import threading
import time

import pytest

from firik_agent.core.runtime import AsyncRuntime
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.controller import MAX_SPEECH_CHARS, VoiceController


def eventually(predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.002)
    assert predicate()


class Speaker:
    def __init__(self, *, block=False):
        self.calls = []
        self.block = block
        self.started = threading.Event()

    def speak(self, text, cancel, language, rate):
        self.calls.append(text)
        self.started.set()
        if self.block:
            cancel.wait(2)


@pytest.fixture
def make_controller():
    runtime = AsyncRuntime()
    instances = []
    def make(speaker=None, **settings):
        async def transcribe(*args):
            return "unused"
        controller = VoiceController(runtime, lambda *args: None, transcribe,
            VoiceSettings(enabled=True, read_replies=True, stream_replies=True, **settings),
            lambda *args: None, lambda *args: None, lambda *args: None,
            speaker=speaker or Speaker(), session_id="stream-session")
        instances.append(controller)
        return controller
    yield make
    for controller in instances:
        assert controller.shutdown(3)
    runtime.shutdown()


def test_sentence_speaks_before_agent_response_completes_without_duplicate_final(make_controller):
    speaker = Speaker()
    controller = make_controller(speaker)
    controller.response_started()
    controller.response_delta("Hello. ")
    assert speaker.started.wait(1)
    assert controller._agent_busy
    controller.response_completed("Hello.")
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["Hello."]


def test_split_fenced_code_and_inline_markdown_are_not_spoken(make_controller):
    speaker = Speaker()
    controller = make_controller(speaker)
    controller.response_started()
    controller.response_delta("First. \n``")
    assert speaker.started.wait(1)
    controller.response_delta("`python\nprivate. code\n``")
    controller.response_delta("`\nThen `internal.")
    controller.response_delta("code` is hidden. ")
    controller.response_completed("full final response must not repeat")
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["First.", "Then is hidden."]


def test_interruption_suppresses_future_deltas_until_next_response(make_controller):
    speaker = Speaker(block=True)
    controller = make_controller(speaker)
    controller.response_started()
    controller.response_delta("First. ")
    assert speaker.started.wait(1)
    controller.stop_speaking()
    controller.response_delta("Must never speak. ")
    controller.response_completed("Must never repeat full response.")
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["First."]
    speaker.block = False
    controller.response_started()
    controller.response_delta("Next response. ")
    controller.response_completed("Next response.")
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["First.", "Next response."]


def test_stream_rejects_excessive_text_and_clears_unplayed_backlog(make_controller):
    speaker = Speaker(block=True)
    controller = make_controller(speaker)
    controller.response_started()
    controller.response_delta("First. ")
    assert speaker.started.wait(1)
    controller.response_delta("word " * (MAX_SPEECH_CHARS // 5 + 1))
    assert controller._stream_cancelled and controller._stream_buffer is None
    controller.response_completed("Full oversized reply must not restart speech.")
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["First."]


def test_stream_queue_is_bounded_and_overflow_cancels_unplayed_sentences(make_controller):
    speaker = Speaker(block=True)
    controller = make_controller(speaker)
    controller.response_started()
    controller.response_delta("First. ")
    assert speaker.started.wait(1)
    controller.response_delta("Queued sentence. " * 20)
    assert controller._stream_cancelled
    assert len(controller._speech_queue) <= 8
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["First."]


def test_final_stable_tail_speaks_once_after_response_completion(make_controller):
    speaker = Speaker()
    controller = make_controller(speaker)
    controller.response_started()
    controller.response_delta("First. Final unpunctuated tail")
    assert speaker.started.wait(1)
    controller.response_completed("First. Final unpunctuated tail")
    eventually(lambda: not controller._workers)
    assert speaker.calls == ["First.", "Final unpunctuated tail"]
