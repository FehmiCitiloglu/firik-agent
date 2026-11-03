# Capability-aware Evren voice — 2 November 2026

Implemented in the existing checkout without replacing its prior voice work.
No commit, installation, release or billable inference request was performed.

## Behavior

- Separate `SpeechToTextProvider` / `TextToSpeechProvider` contracts, normalized
  results, explicit capability evidence and future local-ASR boundary.
- Fresh defaults: disabled voice, Evren ASR `qwen3-asr-1.7b`, Evren TTS
  `voxcpm2`, automatic ASR language, default TTS voice, WAV, speed 1.
  Legacy voice sections without a TTS selection retain system speech.
- Independent ASR/TTS settings leave the selected chat/coding model intact.
  Specialized models are removed from ordinary chat choices; live speech roles
  require explicit task metadata, with conflicting metadata rejected.
- Evren's configured origin and credential scope remain independent of another
  LLM provider. Endpoint/credential changes cancel pending voice work and
  invalidate capability discovery, including requests already in flight.
- Bounded multipart PCM WAV ASR and binary TTS retain the API path allowlist.
  UTF-8, scalar text/SRT and structured results are parsed separately.
- POSTs are never automatically retried. Shared request budgets honor
  `Retry-After`; credit/auth/rate/server/format failures produce safe Turkish
  messages. TTS queue errors reach the UI, and text conversation stays usable.
- System speech is an explicit local selection. Native synthesis retains worker
  ownership through cleanup; remote cancellation discards late results and
  stops queued playback. No automatic alternate-provider transmission occurs.
- Canonical configuration keys load/save alongside legacy aliases. Preferred
  VAD mode affects the accessible toggle without starting capture at startup.
  Wake word, persistent audio retention and voice cloning remain unavailable.

## Validation

| Check | Result |
| --- | --- |
| Broad non-GUI checkpoint: `.venv/bin/python -m pytest -q --ignore-glob='tests/test_ui*.py'` | 1,762 passed, 3 skipped, 138.50 seconds |
| Combined API/voice/controller/engine/config checkpoint | 416 passed |
| API contract and binary CLI tests | 102 passed |
| Final provider/service/catalog follow-up after credential-generation and endpoint-independence fixes | 46 passed |
| Affected native UI cases, separate Tk process per case | 26/26 passed |
| Python 3.10 grammar, 23 source files | Passed |
| `git diff --check` | Passed |

The broad checkpoint preceded the final small provider/discovery fixes; the
follow-up covers those changes. Native UI tests inject audio/HTTP and never
record a microphone or use live inference. One initial native failure was a
stale credential mock; its corrected isolated rerun passed. Initial and rerun
logs are retained in this directory, with final statuses in
`provider-desktop-results.json`.

## Live discovery and remaining verification

The public OpenAPI and Swagger UI were readable. ASR/TTS paths, request fields,
formats, default voice and speed range are documented. `/v1/models` returned
401 without authentication; the existing credential lookup yielded no available
key. See `API_DISCOVERY.md` and `evren-audio-contract.json`.

Authenticated model access/inference, actual media types, Turkish/English speech
quality, server limits, prices, streaming, device accuracy, acoustic behavior and
packaged microphone permissions remain unverified. Client limits and mock tests
are not evidence of those capabilities. No streaming speech or model-backed
SystemOne command classifier was enabled.
