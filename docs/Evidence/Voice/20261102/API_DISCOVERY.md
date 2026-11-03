# Evren audio contract discovery — 2 November 2026

Read-only requests to the configured Evren origin:

| Request | Result |
| --- | --- |
| `GET https://evren-llmapi.ssyz.org.tr/openapi.json` | 200, `application/json` |
| `GET https://evren-llmapi.ssyz.org.tr/docs` | 200, Swagger UI HTML |
| `GET https://evren-llmapi.ssyz.org.tr/v1/models` | 401, `authentication_error`, `API anahtarı eksik` |

No configured credential was available through the existing environment/native
credential-store lookup. No inference POST was sent. The public schema and its
relevant declarations are captured in `evren-audio-contract.json`.

The public schema verifies both `POST /v1/audio/transcriptions` and
`POST /v1/audio/speech`. Only the public speech path and its request schema were
added to the local path allowlist; internal operations were not copied.

ASR is multipart with required `file` and `model`, optional ISO 639-1 `language`
hint, `prompt`, `response_format` (`json`, `text`, `srt`, `verbose_json`,
`diarized_json`), and `temperature` (description: 0–1). Automatic language omits
the hint. The response schema is unconstrained; normalized language and duration
remain absent unless supplied by the response. Confidence, segments and timing
stay metadata and never become recognized text.

TTS is JSON with required `model` and `input`. The schema names `voxcpm2` as an
example. `voice` defaults to `default`; OpenAI aliases are described as mapping
to that same voice, so the client exposes only `default`. Formats: `mp3`, `wav`,
`flac`, `opus`, `pcm`; default server format is MP3, while the desktop requests
WAV for playback. `speed` is 0.25–4.0. `instructions` is an optional English voice
description. `stream=true` requires raw PCM; this implementation deliberately
uses bounded non-streaming requests. PCM is described as raw 16-bit mono 48 kHz.
There is no language or reference-audio/voice-cloning field.

Unverified: authenticated model access/task metadata, successful inference,
server response media types, audible output, Turkish/English quality, actual
server text/audio size/duration limits, idempotency and streaming behavior. The
public schema defines no security scheme; `X-API-Key` is retained from the
existing client, while authenticated acceptance could not be tested.

Client budgets are explicit local policies, not provider claims: PCM WAV input
only, at most 25 MiB/120 seconds, 256 KiB transcription response, 1600 synthesis
characters/8192 UTF-8 bytes, 1000 style-instruction characters, 25 MiB synthesis
response (120 seconds additionally enforced for WAV/PCM). Audio POSTs are never
automatically retried; timeouts may already represent billable work. All
integration validation uses `httpx.MockTransport` and synthetic local audio.
