# AI4Bharat speech service

Kannada speech-to-text, text-to-speech and translation for KSP-CIP, running on
hardware you control. Separate from the backend on purpose: these models pull
in PyTorch and several gigabytes of weights, and the Catalyst deployment
artifact must not carry that for a feature most requests never touch.

The backend reaches it through `AI4BharatLanguageService` and falls back to the
offline Kannada glossary whenever it is unreachable — a speech outage degrades
the answer's *language fidelity*, never its facts or its evidence.

## Run it

```bash
cd speech-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 9100
```

Point the backend at it:

```bash
export KSPCIP_LANGUAGE_PROVIDER=ai4bharat
export KSPCIP_AI4BHARAT_BASE_URL=http://127.0.0.1:9100
```

`ffmpeg` must be on `PATH` to decode browser audio (WebM/Opus):
`brew install ffmpeg` or `sudo apt-get install ffmpeg`. Plain mono 16 kHz WAV
decodes without it.

## Wiring mode (no models, no GPU)

To exercise the full round trip — backend → service → owner-scoped audio URL —
on a laptop with no weights downloaded:

```bash
SPEECH_STUB_MODE=1 uvicorn app:app --port 9100
```

Every response is then marked `"stub": true` and `/health` reports
`"stub_mode": true`. It returns placeholder text and silent audio. It is for
testing plumbing and is never a real transcript — do not demo from it.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | status, stub mode, whether ffmpeg is present, which models are loaded |
| `POST /asr` | `{audio_base64, audio_format, language, model}` → `{text, ...}` |
| `POST /tts` | `{text, language, model, speaker}` → `{audio_base64, sample_rate, ...}` |
| `POST /translate` | `{text, source, target}` → `{text, ...}` |

## Models

| Task | Default | Override |
|---|---|---|
| ASR | `ai4bharat/indic-conformer-600m-multilingual` | `SPEECH_ASR_MODEL` |
| TTS | `ai4bharat/indic-parler-tts` | `SPEECH_TTS_MODEL` |
| en→indic | `ai4bharat/indictrans2-en-indic-dist-200M` | `SPEECH_NMT_EN_INDIC` |
| indic→en | `ai4bharat/indictrans2-indic-en-dist-200M` | `SPEECH_NMT_INDIC_EN` |

Models load on **first use**, not at startup — a cold start that blocks for
minutes downloading weights is harder to operate than a first request that is
slow and says why. Expect the first `/asr` and `/tts` call after a restart to
take a while.

## Hardware

| Scale | Suggested |
|---|---|
| Development | CPU, 16 GB RAM |
| Small (10–20 users) | RTX 3060/4060, 8–12 GB VRAM |
| Medium (50–100 users) | RTX 4090 / L40S / A100 |
| Large | multiple instances behind a load balancer |

CPU works for a demo; it is not real-time.

## Honest limitations

- **No domain benchmark yet.** These checkpoints have not been measured against
  this project's Kannada policing vocabulary — crime and legal terms, Karnataka
  place names, transliterated proper nouns. Accuracy on those specifically is
  unknown until a small labelled set exists, in the same way entity resolution
  was calibrated before its thresholds were trusted.
- **No authentication.** Bind it to loopback or a private network. It performs
  no authorization of its own; the backend authorizes the caller before any
  audio reaches this service.
- **Raw audio is not persisted** by this service or by the backend. Only the
  transcript is kept.
