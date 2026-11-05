"""Opt-in Evren smoke test with a fixed phrase and no audio devices."""
import os
import re

import pytest

from firik_agent.api.client import DEFAULT_BASE_URL, FirikAPI
from firik_agent.voice.errors import VoiceAudioError
from firik_agent.voice.providers import EvrenSpeechToTextProvider, EvrenTextToSpeechProvider, VoiceRequestGate


@pytest.mark.asyncio
async def test_live_evren_synthesis_and_transcription_round_trip(pytestconfig):
    if not pytestconfig.getoption("--run-live-evren-audio"):
        pytest.skip("Requires --run-live-evren-audio and an explicit smoke-test API key.")
    key = os.environ.get("FIRIK_LIVE_AUDIO_API_KEY", "").strip()
    if not key:
        pytest.fail("Set FIRIK_LIVE_AUDIO_API_KEY for this explicitly enabled smoke test.", pytrace=False)

    def client_factory():
        return FirikAPI(key, DEFAULT_BASE_URL, timeout=60)

    gate = VoiceRequestGate()
    tts = EvrenTextToSpeechProvider(client_factory, endpoint=DEFAULT_BASE_URL, gate=gate)
    asr = EvrenSpeechToTextProvider(client_factory, endpoint=DEFAULT_BASE_URL, gate=gate)
    try:
        speech = await tts.synthesize("Hello. This is a voice test.", language="en")
    except VoiceAudioError as error:
        pytest.fail(f"Evren TTS failed. {error}", pytrace=False)
    try:
        transcript = await asr.transcribe(speech.audio, language="en")
    except VoiceAudioError as error:
        pytest.fail(f"Evren ASR failed on synthesized speech. {error}", pytrace=False)
    words = set(re.findall(r"[a-z]+", transcript.text.lower()))
    if not {"voice", "test"} <= words:
        pytest.fail("Evren ASR did not recognize the expected words in the fixed smoke-test phrase.", pytrace=False)
