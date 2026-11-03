"""PortAudio device discovery and fail-closed saved selection resolution.

PortAudio indexes are session-local. Saved IDs identify host API + device name;
duplicate names are deliberately ambiguous rather than selecting a different mic.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib

from firik_agent.voice.errors import VoiceAudioError, VoiceDeviceError


def load_backend():
    try:
        return importlib.import_module("sounddevice")
    except (ImportError, OSError) as error:
        raise VoiceAudioError("Mikrofon desteği yüklenemedi. Ses desteğini 'pip install firik-agent[voice]' ile kurun; Linux'ta PortAudio paketinin de kurulu olduğunu kontrol edin.") from error


@dataclass(frozen=True)
class AudioDevice:
    id: str
    index: int
    name: str
    host_api: str
    channels: int
    default_sample_rate: float
    is_default: bool = False


class AudioDeviceCatalog:
    def __init__(self, backend=None):
        self._backend = backend

    @property
    def backend(self):
        return self._backend if self._backend is not None else load_backend()

    def list_devices(self, kind="input"):
        if kind not in ("input", "output"):
            raise ValueError("device kind must be input or output")
        try:
            backend = self.backend
            hosts = backend.query_hostapis()
            default = backend.default.device[0 if kind == "input" else 1]
            result = []
            for index, info in enumerate(backend.query_devices()):
                channels = int(info.get("max_" + kind + "_channels", 0))
                if channels < 1:
                    continue
                host = str(hosts[int(info["hostapi"])]["name"])
                name = str(info["name"])
                identity = hashlib.sha256((host + "\0" + name).encode("utf-8")).hexdigest()[:24]
                result.append(AudioDevice("pa:" + identity, index, name, host, channels,
                                          float(info["default_samplerate"]), index == default))
            return tuple(result)
        except VoiceAudioError:
            raise
        except Exception as error:
            raise VoiceDeviceError("Ses aygıtları okunamadı. Sistem ses ayarlarını kontrol edin.") from error

    def resolve(self, selection=None, kind="input"):
        if selection is None:
            return None  # Resolve the system default at each stream open.
        devices = self.list_devices(kind)
        if isinstance(selection, bool) or not isinstance(selection, (int, str)):
            raise VoiceDeviceError("Geçerli bir ses aygıtı seçin.")
        if isinstance(selection, int):
            matches = [d for d in devices if d.index == selection]
        elif selection.startswith("pa:"):
            matches = [d for d in devices if d.id == selection]
        else:
            # Legacy exact names remain readable, but substring matching can
            # silently select a different microphone and is never used.
            matches = [d for d in devices if d.name == selection]
        if len(matches) != 1:
            raise VoiceDeviceError("Seçilen ses aygıtı bulunamadı veya adı belirsiz. Ses ayarlarından yeniden seçin.")
        return matches[0].index
