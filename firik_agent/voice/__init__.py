"""Optional voice interaction. Importing this package never opens audio devices."""

from firik_agent.voice.commands import VoiceCommand, parse_command
from firik_agent.voice.config import VoiceSettings
from firik_agent.voice.controller import VoiceController
from firik_agent.voice.models import AudioFormat, PCMBuffer
from firik_agent.voice.session import VoiceState
from firik_agent.voice.providers import SpeechToTextProvider, TextToSpeechProvider, SpeechCapabilities
from firik_agent.voice.intent_router import VoiceRequestRouter
from firik_agent.voice.requests import VoiceRequest, VoiceScope, VoiceImpact, VoiceCategory, VoiceConfirmation

__all__ = ["AudioFormat", "PCMBuffer", "VoiceCommand", "VoiceController", "VoiceSettings", "VoiceState", "parse_command",
           "SpeechToTextProvider", "TextToSpeechProvider", "SpeechCapabilities",
           "VoiceRequestRouter", "VoiceRequest", "VoiceScope", "VoiceImpact", "VoiceCategory", "VoiceConfirmation"]
