# Kannada voice via self-hosted AI4Bharat (V2.1 M1-01/M1-03)

Kannada speech-to-text and text-to-speech running on hardware the deployment
controls, instead of a credentialed call to a hosted service.

`implementationv2.1.md` M1 assumed Bhashini, which is blocked on credentials
and approval that cannot be obtained from a developer machine. AI4Bharat
publishes the model *weights*, so the same capability becomes a hosting
question rather than a procurement one. Both providers remain selectable; this
document covers the AI4Bharat path.

---

## 1. Why self-hosting, beyond cost

- **Residency.** FIR audio never leaves the machines running the platform.
  Bhashini keeps it inside a government service; self-hosting keeps it inside
  *this* deployment, which is strictly stronger for case data.
- **No quota, no third-party outage** to inherit.
- **The blocker changes shape.** "Waiting for approved credentials" becomes
  "stand up a box," which a team can actually do.

What it does *not* change: the offline Kannada glossary remains the
zero-credential default, and every claim about fidelity is still reported
rather than assumed.

---

## 2. Shape

```
React console ──audio──▶ FastAPI /chat/transcribe
                              │
                     ConversationLanguageService
                              │
                     LanguageService port  ◀── the seam; nothing above it
                              │                 knows which provider answered
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
      LocalLexicon      Bhashini        AI4Bharat  ──HTTP──▶ speech-service/
      (default,          (hosted)       (self-hosted)         ASR · TTS · NMT
       glossary)                                              (PyTorch, GPU)
                              │
                     Kannada → English text
                              │
              (unchanged) SupervisorAgent → five agents → AnswerComposer
                              │
                    evidenced answer, then translated back
                              │
                     TTS ──▶ audio/<user_id>/<session>/<sha256>.wav
                              │
                     owner-authorized /files/ download
```

The five agents, the analytics and the evidence composer are untouched. Speech
is an **I/O boundary**: audio becomes text before routing, and text becomes
audio only after the answer has been composed, evidenced and verified. That
ordering is what keeps ADR-0003 intact — the model never produces a figure,
and speech never creates a second, unverified path to an answer.

---

## 3. Running it

The speech models live in `speech-service/`, a **separate deployable**. Keeping
PyTorch out of `backend/pyproject.toml` is deliberate: it would otherwise land
in every Catalyst artifact for a feature most requests never touch.

```bash
cd speech-service
pip install -r requirements.txt
uvicorn app:app --port 9100
```

```bash
export KSPCIP_LANGUAGE_PROVIDER=ai4bharat
export KSPCIP_AI4BHARAT_BASE_URL=http://127.0.0.1:9100
scripts/run.sh
```

`ffmpeg` must be on `PATH` for browser audio (WebM/Opus). Mono 16 kHz WAV
decodes without it.

To exercise the wiring with no models and no GPU, run the service with
`SPEECH_STUB_MODE=1`. Responses are then marked `"stub": true` and `/health`
reports `stub_mode: true`. It proves the round trip; it is not a transcript.

### Settings

| Variable | Default | Notes |
|---|---|---|
| `KSPCIP_LANGUAGE_PROVIDER` | `local` | `ai4bharat` selects this path |
| `KSPCIP_AI4BHARAT_BASE_URL` | *(unset)* | **required**; startup fails without it |
| `KSPCIP_AI4BHARAT_TIMEOUT_SECONDS` | `60` | |
| `KSPCIP_AI4BHARAT_ASR_MODEL` | `ai4bharat/indic-conformer-600m-multilingual` | |
| `KSPCIP_AI4BHARAT_TTS_MODEL` | `ai4bharat/indic-parler-tts` | |
| `KSPCIP_AI4BHARAT_TTS_SPEAKER` | `female` | |
| `KSPCIP_VOICE_MAX_AUDIO_BYTES` | `10485760` | provider-neutral; enforced at the endpoint *and* the adapter |

There is no API key, because there is no vendor account.

---

## 4. What happens when it breaks

Degradation is per-capability, because the honest answer differs by capability.

| Failure | Behaviour | Why |
|---|---|---|
| Service unreachable, **translate** | Falls back to the offline glossary | A partly-translated answer is useful and is labelled as glossary-quality |
| Service unreachable, **synthesize** | Returns `None`; the answer ships without audio | Losing the recording must not lose the answer |
| Service unreachable, **transcribe** | Raises `ProviderError` (502) | There is no honest substitute for a transcript. Guessing would fabricate the user's question |
| Malformed JSON / HTTP error | Typed `ProviderError`, never a partial parse | |
| Audio over the byte ceiling | Refused **before** any network call | An untrusted body must not be forwarded to a model. Note this bounds what the handler and provider hold, not what the socket accepted — a true ingress limit belongs in the server or proxy |
| Unsupported container | Refused at the boundary with the supported list | Fails clearly instead of deep inside a model loader |

`language_full_fidelity` in `/api/v1/health` and `/api/v1/capabilities` tells
the console which of these is in force, and the console only requests audio
when it is `true`.

---

## 5. Security properties

- **Raw audio is never persisted.** Only the transcript is kept.
- **Synthesised audio is owner-scoped.** Keys are
  `audio/<user_id>/<session>/<sha256>.wav` and served only through
  `authorize_file_access`. Verified end to end: the owner gets 200, another
  user 403, an anonymous caller 401.
- **The console fetches audio with its bearer token** and plays it as a blob.
  A plain `<audio src>` could not authenticate and would 401.
- **The speech service performs no authorization of its own.** The backend
  authorizes the caller before any audio reaches it. Bind it to loopback or a
  private network.

### A bug this work fixed

TTS audio was written to `audio/<session_id>/…`, but ownership is attributed
from the *first* path segment. The object therefore resolved to no owner and
`authorize_file_access` refused to serve it — **to everyone, including the user
who had just requested it**. It was invisible because no configured provider
ever returned audio bytes to write; it would have fired the moment one did.
The key also used `hash()`, which is salted per interpreter, so the same answer
produced a different key on every restart. Both are fixed and pinned by
`TestSynthesisedAudioIsOwned` in `backend/tests/unit/test_file_access.py`.

---

## 6. Honest limitations

- **No domain benchmark.** These checkpoints have not been measured against
  this project's Kannada policing vocabulary — crime and legal terms,
  Karnataka place names, transliterated proper nouns. Accuracy on those
  specifically is **unknown** until a small labelled set exists. The entity
  resolver was calibrated before its thresholds were trusted; this deserves
  the same treatment before anyone relies on it operationally.
- **Not verified against real weights here.** Every path in this document was
  exercised against the stub service and the contract tests. The model-loading
  code in `speech-service/app.py` has not been run with the multi-gigabyte
  checkpoints downloaded — no GPU was available. The HTTP contract on both
  sides is tested; the model calls themselves are not.
- **Batch, not streaming.** A question is recorded, then sent. The partial-
  transcript streaming design is not implemented.
- **`/translate` needs IndicTransToolkit.** Without it the service returns 503
  and the platform falls back to the glossary — the designed degradation.
- **Kannada and English only** in this build, though the models cover far more
  Indic languages. Widening it is a configuration and evaluation question.

---

## 7. Tests

| Concern | Where |
|---|---|
| Adapter contract, degradation, validation (27 tests) | `backend/tests/unit/test_ai4bharat_language.py` |
| Audio ownership + key stability | `backend/tests/unit/test_file_access.py::TestSynthesisedAudioIsOwned` |
| Endpoint input validation | `backend/tests/integration/test_api.py::TestTranscribeEndpoint` |

All run offline — the HTTP transport is faked at `urlopen`, the same way the
Catalyst adapter is contract-tested.
