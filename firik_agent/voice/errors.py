"""Safe, typed failures at optional audio subsystem boundaries."""


class VoiceAudioError(RuntimeError):
    """An actionable audio failure safe to display to the user."""


class VoicePermissionError(VoiceAudioError):
    """Microphone permission was explicitly denied."""


class VoiceDeviceError(VoiceAudioError):
    """The selected device is unavailable, ambiguous, or disconnected."""


class VoiceOverflowError(VoiceAudioError):
    """A bounded audio queue or native buffer overflowed."""


class VoiceFormatError(VoiceAudioError):
    """PCM or its container does not satisfy the audio contract."""


class VoicePlaybackError(VoiceAudioError):
    """Output audio could not be played."""


class VoiceSynthesisError(VoiceAudioError):
    """Speech could not be synthesized."""
