"""Actual recognized language survives finalized transcription and UI delivery."""
import io
from types import SimpleNamespace
import wave

import pytest

from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.controller import VoiceController, VoiceState
from firik_agent.voice.transcription import FinalTranscript, TranscriptionAdapter, TranscriptionPolicy


def audio():
    result = io.BytesIO()
    with wave.open(result, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x01\x00' * 16000)
    return result.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize('languages,expected', [(['tr','tr'],'tr'),(['en','tr'],None),(['en',None],None)])
async def test_segments_report_language_only_when_provider_evidence_is_consistent(languages, expected):
    pending = list(languages)
    async def request(*args):
        return FinalTranscript('recognized prose', pending.pop(0))
    result = await TranscriptionAdapter(request, TranscriptionPolicy(max_request_seconds=.5)).transcribe(audio(), 'asr', 'en')
    assert result == 'recognized prose recognized prose'
    assert result.recognized_language == expected


def test_controller_delivers_actual_language_without_using_configured_hint():
    delivered = []
    async def transcribe(*args):
        raise AssertionError('no inference needed')
    controller = VoiceController(SimpleNamespace(), lambda callback,*args:callback(*args), transcribe,
        VoiceSettings(enabled=True, language='en'), lambda _:None, delivered.append, lambda _:None,
        session_id='existing-session')
    try:
        controller._state = VoiceState.TRANSCRIBING
        controller._transcription_done(controller.generation, FinalTranscript('  Merhaba  ', 'tr'))
        assert delivered[0] == 'Merhaba' and delivered[0].recognized_language == 'tr'
    finally:
        controller.close()
