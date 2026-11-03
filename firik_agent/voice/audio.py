"""Compatibility imports for the modular optional input engine."""
import importlib  # Retained as a dependency-injection seam for existing clients.
from firik_agent.voice.audio_capture import PCMResampler, SoundDeviceRecorder
from firik_agent.voice.errors import VoiceAudioError, VoicePermissionError
from firik_agent.voice.models import MAX_AUDIO_BYTES
__all__ = ['SoundDeviceRecorder', 'PCMResampler', 'VoiceAudioError', 'VoicePermissionError', 'MAX_AUDIO_BYTES']
