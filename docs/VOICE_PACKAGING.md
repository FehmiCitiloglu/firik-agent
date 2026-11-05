# Voice packaging and device verification

Build on each target OS with Python and Tk installed. The `desktop-build` extra
installs sounddevice and PyInstaller. Add `voice-vad` or `voice-codecs` to a
development build when you need those optional backends.

```sh
python -m pip install -e ".[desktop-build]"
python -m PyInstaller firik-agent.spec --clean --noconfirm
python scripts/smoke_desktop.py
```

On Linux, install PortAudio before building. For example, on Debian/Ubuntu use
`sudo apt-get install libportaudio2 python3-tk`. Use
`xvfb-run -a python scripts/smoke_desktop.py` on a headless runner with Xvfb and
xauth installed. For source installs, `python -m sounddevice` lists the devices
the backend sees. An empty list means the library loaded and the session exposes
no audio devices. Check the system default and the session's ALSA or audio-server
configuration. For a missing library, install the distribution's PortAudio
runtime and restart the app. [sounddevice installation guide](https://python-sounddevice.readthedocs.io/en/latest/installation.html)

On Windows, use a Python interpreter that matches the installer architecture.
Check **Settings → System → Sound → Input** for the device and **Settings →
Privacy & security → Microphone** for desktop app access. If the packaged
backend fails while the source backend works, run the frozen smoke test and
verify that `SD_ENABLE_ASIO` is unset in the launching environment. The packaged
build uses the standard PortAudio DLL. [Windows microphone settings](https://support.microsoft.com/en-us/windows/privacy/turn-on-app-permissions-for-your-microphone-in-windows)

On macOS, launch the actual `.app` in a desktop login session and check
**System Settings → Privacy & Security → Microphone** for that app. A source run
uses the permissions associated with its Python or terminal launcher. Inspect
the built bundle's microphone purpose string and signed input entitlement with
the commands below. The release signing procedure is documented separately.
[macOS microphone settings](https://support.apple.com/guide/mac-help/control-access-to-the-microphone-mchla1b1e1fe/mac)
[Developer ID signing](MACOS_SIGNING.md)

```sh
plutil -extract NSMicrophoneUsageDescription raw dist/firik-agent.app/Contents/Info.plist
codesign -d --entitlements - dist/firik-agent.app
codesign --verify --deep --strict dist/firik-agent.app
```

The release smoke runs from an isolated profile and enumerates input and output
devices through the frozen PortAudio backend. It opens zero streams and accepts
an empty device list. For a deliberately audio-free bundle, use
`python scripts/smoke_desktop.py --text-only`. If using a custom build directory,
pass `--distpath PATH`.

For the manual device check, use a test account or an environment with no
provider credential. Start the app and confirm the microphone indicator stays
off. Enable voice and explicitly start recording. On a fresh macOS permission
profile, check that the permission prompt appears at this action. Deny it,
confirm that capture ends with an actionable error, then grant access and retry.
On Windows, repeat with desktop microphone access disabled and enabled. On Linux,
repeat with no input device, then connect one and select it in voice settings.
Cancel during the visible recording state and confirm the OS indicator turns off.
This procedure exercises device access and cleanup without submitting an utterance.

The macOS arm64 development bundle built on 4 November 2026 passed ad-hoc
signature validation, microphone metadata inspection, and the frozen device
enumeration and startup/shutdown smoke. Hardware capture, the OS permission
prompt, Developer ID distribution, and native Windows/Linux checks require
their respective test environments.

Run the following checklist on each target OS. Record OS version, app version,
source versus frozen build, selected devices, outcome and error guidance.
Use a disposable workspace for agent actions. Speech transcription, remote
speech output and agent requests send data to their configured services and
may incur charges.

| Scenario | Manual action and expected result |
| --- | --- |
| A | Disconnect input devices and launch. Type in an existing chat. The composer remains responsive and the microphone stays inactive. |
| B | Reconnect/select a microphone, enable voice, open an existing chat and hold **🎙 Ses**. Confirm the app recording state and OS microphone indicator. Release to finalize. |
| C | Ask a Turkish question. Check the final transcript in review, then press **Gönder / Uygula**. Confirm the user message appears once in the selected conversation. Test ordinary auto-send separately after explicitly enabling it. |
| D | Keep the selected conversation/coding LLM, select Evren ASR/TTS and enable response reading. Confirm the ordinary assistant text and optional spoken response. Record actual model names and language quality. |
| E | Stop speaking during playback. Sound stops, the text remains, and unrelated agent work continues. Repeat with pause/resume and a new explicit microphone action. |
| F | Switch chats while ASR is pending. The old result must reach neither the new chat nor its composer. Repeat while changing projects and provider settings. |
| G | In an existing project's coding session, ask to summarize its current tests. Check the selected root/model and ordinary coding activity. Compare the concise spoken summary with actual recorded results. |
| H | Request deletion of a specific disposable file. Review the transcript, then reject the separate operation approval. The file remains. Spoken approval phrases cannot approve it. |
| I | Revoke input permission and explicitly retry capture. Confirm guidance and responsive text input. Grant permission and explicitly retry. |
| J | Disconnect networking during ASR and TTS separately. Confirm an actionable error and working text composer. Check that inference is never automatically retried. |
| K | Close while capturing, transcribing, synthesizing, playing and testing input/output. OS recording/output indicators stop. Relaunch and repeat ten times, checking for orphan processes or growing audio-worker counts. |
| L | Run the default pytest commands in the voice guide with provider keys absent and all devices disconnected. The live smoke skips. |

For device checks, explicitly run **Mikrofon düzeyini sına** and **Hoparlörü sına**
with the selected microphone/speaker and unsaved gain/volume. Stop each test,
change tabs/devices, save, hide and close settings. Delayed callbacks must leave
the finished form alone. Remove a device during each test, reconnect it and
refresh the selection. The speaker test plays a bounded synthetic tone locally.

On macOS, test source Python/Terminal and the signed application separately,
including first consent, denial and re-grant in Privacy & Security. On Windows,
test desktop microphone access, the selected WASAPI/MME device and DLL loading
from the frozen install. On Linux, test an absent PortAudio library, a loaded
backend with no devices, and actual ALSA/PulseAudio/PipeWire access under the
user's desktop session. Source runs and stubbed platform tests establish only
the behavior they exercise.
