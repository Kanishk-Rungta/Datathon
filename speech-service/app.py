"""AI4Bharat speech service — ASR, TTS and translation over HTTP.

This is a **separate deployable** from the KSP-CIP backend, on purpose. The
AI4Bharat models pull in PyTorch and several gigabytes of weights; putting
that in the API worker would place a multi-gigabyte dependency into every
Catalyst deployment artifact for a feature most requests never touch. The
backend talks to this service over HTTP through
``ksp_cip.infrastructure.language.ai4bharat.AI4BharatLanguageService`` and
degrades to the offline Kannada glossary whenever it is unreachable.

Run it:

    pip install -r requirements.txt
    uvicorn app:app --host 127.0.0.1 --port 9100

Then point the backend at it:

    export KSPCIP_LANGUAGE_PROVIDER=ai4bharat
    export KSPCIP_AI4BHARAT_BASE_URL=http://127.0.0.1:9100

Models load lazily on first use, not at startup: a cold start that blocks for
several minutes downloading weights is far harder to operate than a first
request that is slow and says why.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import shutil
import subprocess
import threading
import wave
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

LOGGER = logging.getLogger("speech-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

#: Sample rate every AI4Bharat ASR checkpoint in this service expects.
TARGET_SAMPLE_RATE = 16_000

#: Explicit, loudly-reported echo mode. It exists so the *wiring* (backend →
#: service → backend, ownership, URLs, error paths) can be exercised on a
#: laptop with no models and no GPU. It never pretends to be a real
#: transcript: every response it produces is marked ``stub: true`` and
#: ``/health`` reports ``stub_mode: true``.
STUB_MODE = os.environ.get("SPEECH_STUB_MODE", "").strip().lower() in {"1", "true", "yes"}

DEFAULT_ASR_MODEL = os.environ.get("SPEECH_ASR_MODEL", "ai4bharat/indic-conformer-600m-multilingual")
DEFAULT_TTS_MODEL = os.environ.get("SPEECH_TTS_MODEL", "ai4bharat/indic-parler-tts")
DEFAULT_NMT_EN_INDIC = os.environ.get("SPEECH_NMT_EN_INDIC", "ai4bharat/indictrans2-en-indic-dist-200M")
DEFAULT_NMT_INDIC_EN = os.environ.get("SPEECH_NMT_INDIC_EN", "ai4bharat/indictrans2-indic-en-dist-200M")

#: AI4Bharat models are keyed by BCP-47-ish codes with an explicit script.
FLORES_CODES = {"kn": "kan_Knda", "en": "eng_Latn"}

app = FastAPI(title="AI4Bharat speech service", version="1.0.0")

_LOCK = threading.Lock()
_MODELS: dict[str, Any] = {}


# --------------------------------------------------------------------- audio


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def decode_to_pcm16k(payload: bytes, audio_format: str) -> "Any":
    """Decode arbitrary browser audio into a mono 16 kHz float32 waveform.

    Browsers hand back WebM/Opus, which no audio library in the Python
    scientific stack decodes reliably on its own, so ffmpeg does the container
    and resampling work. A plain 16 kHz mono WAV skips ffmpeg entirely, which
    keeps the common server-to-server case dependency-free.
    """
    import numpy as np

    if audio_format == "wav" and not _have_ffmpeg():
        with wave.open(io.BytesIO(payload), "rb") as handle:
            if handle.getnchannels() != 1 or handle.getframerate() != TARGET_SAMPLE_RATE:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        "ffmpeg is not installed, so only mono 16 kHz WAV can be decoded. "
                        f"Received {handle.getnchannels()} channel(s) at {handle.getframerate()} Hz."
                    ),
                )
            frames = handle.readframes(handle.getnframes())
        return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

    if not _have_ffmpeg():
        raise HTTPException(
            status_code=503,
            detail=f"Decoding '{audio_format}' needs ffmpeg on PATH, and it is not installed.",
        )

    process = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
         "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE), "pipe:1"],
        input=payload, capture_output=True, check=False,
    )
    if process.returncode != 0:
        detail = process.stderr.decode("utf-8", errors="replace")[:300]
        raise HTTPException(status_code=400, detail=f"Could not decode audio: {detail}")
    return np.frombuffer(process.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def pcm_to_wav_bytes(samples: "Any", sample_rate: int) -> bytes:
    import numpy as np

    clipped = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return buffer.getvalue()


# -------------------------------------------------------------------- models


def _get_device() -> str:
    """Detect available hardware accelerator (CUDA -> MPS -> CPU)."""
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _get_gpu_info() -> dict[str, Any]:
    """Inspect GPU hardware and VRAM allocation if CUDA is active."""
    try:
        import torch

        if torch.cuda.is_available():
            device_name = torch.cuda.get_device_name(0)
            allocated = torch.cuda.memory_allocated(0) // (1024 * 1024)
            total = torch.cuda.get_device_properties(0).total_memory // (1024 * 1024)
            return {
                "gpu_available": True,
                "device_name": device_name,
                "vram_allocated_mb": allocated,
                "vram_total_mb": total,
                "fp16_supported": True,
            }
    except Exception:  # noqa: BLE001
        pass
    return {
        "gpu_available": False,
        "device_name": None,
        "vram_allocated_mb": 0,
        "vram_total_mb": 0,
        "fp16_supported": False,
    }


def _load(key: str, builder: Any) -> Any:
    """Lazily build a model once, under a lock, and cache it for the process."""
    if key in _MODELS:
        return _MODELS[key]
    with _LOCK:
        if key not in _MODELS:
            LOGGER.info("loading model %s (first use — this can take several minutes)", key)
            _MODELS[key] = builder()
            LOGGER.info("loaded model %s", key)
    return _MODELS[key]


def asr_model(name: str) -> Any:
    def build() -> Any:
        import torch
        from transformers import AutoModel

        model = AutoModel.from_pretrained(name, trust_remote_code=True)
        model.eval()
        device = _get_device()
        if device != "cpu":
            model = model.to(device)
            LOGGER.info("ASR model %s placed on %s", name, device)
        return model

    return _load(f"asr:{name}", build)


def tts_model(name: str) -> Any:
    def build() -> Any:
        import torch
        from parler_tts import ParlerTTSForConditionalGeneration
        from transformers import AutoTokenizer

        model = ParlerTTSForConditionalGeneration.from_pretrained(name)
        model.eval()
        device = _get_device()
        if device != "cpu":
            model = model.to(device)
            LOGGER.info("TTS model %s placed on %s", name, device)
        return {
            "model": model,
            "tokenizer": AutoTokenizer.from_pretrained(name),
            "description_tokenizer": AutoTokenizer.from_pretrained(model.config.text_encoder._name_or_path),
            "device": device,
        }

    return _load(f"tts:{name}", build)


def nmt_model(name: str) -> Any:
    def build() -> Any:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(name, trust_remote_code=True)
        model.eval()
        device = _get_device()
        if device != "cpu":
            model = model.to(device)
            LOGGER.info("NMT model %s placed on %s", name, device)
        return {"model": model, "tokenizer": tokenizer, "device": device}

    return _load(f"nmt:{name}", build)


# ------------------------------------------------------------------ schemas


class ASRRequest(BaseModel):
    audio_base64: str
    audio_format: str = "wav"
    language: str = "kn"
    model: str = DEFAULT_ASR_MODEL


class ASRResponse(BaseModel):
    text: str
    language: str
    model: str
    stub: bool = False


class TTSRequest(BaseModel):
    text: str = Field(min_length=1)
    language: str = "kn"
    model: str = DEFAULT_TTS_MODEL
    speaker: str = "female"


class TTSResponse(BaseModel):
    audio_base64: str
    sample_rate: int
    model: str
    stub: bool = False


class TranslateRequest(BaseModel):
    text: str
    source: str = "en"
    target: str = "kn"


class TranslateResponse(BaseModel):
    text: str
    source: str
    target: str
    model: str
    stub: bool = False


# ----------------------------------------------------------------- endpoints


@app.get("/health")
def health() -> dict[str, Any]:
    gpu_info = _get_gpu_info()
    return {
        "status": "ok",
        "stub_mode": STUB_MODE,
        "ffmpeg": _have_ffmpeg(),
        "device": _get_device() if not STUB_MODE else "stub",
        "gpu": gpu_info,
        "loaded_models": sorted(_MODELS),
        "defaults": {
            "asr": DEFAULT_ASR_MODEL,
            "tts": DEFAULT_TTS_MODEL,
            "nmt_en_indic": DEFAULT_NMT_EN_INDIC,
            "nmt_indic_en": DEFAULT_NMT_INDIC_EN,
        },
        "notice": (
            "STUB MODE — responses are placeholders for wiring tests and are not real "
            "transcription or speech." if STUB_MODE else
            "Models load on first use; the first request after start is slow."
        ),
    }


from fastapi import WebSocket, WebSocketDisconnect


@app.websocket("/ws/stream-asr")
async def websocket_stream_asr(websocket: WebSocket, language: str = "kn", model: str = DEFAULT_ASR_MODEL) -> None:
    """Real-time streaming speech-to-text WebSocket endpoint.

    Accepts raw PCM or WAV audio bytes over WebSocket, decodes in chunks, and streams
    partial transcripts back to the client in real time.
    """
    await websocket.accept()
    buffer = bytearray()
    try:
        while True:
            data = await websocket.receive_bytes()
            if not data:
                break
            buffer.extend(data)

            if STUB_MODE:
                await websocket.send_json({
                    "text": f"[stub real-time transcript: {len(buffer)} bytes received]",
                    "is_final": False,
                    "stub": True,
                })
            elif len(buffer) >= 16000:
                try:
                    waveform = decode_to_pcm16k(bytes(buffer), "wav")
                    import torch

                    model_inst = asr_model(model)
                    tensor = torch.from_numpy(waveform).unsqueeze(0)
                    device = _get_device()
                    if device != "cpu":
                        tensor = tensor.to(device)
                    with torch.no_grad():
                        result = model_inst(tensor, language, "ctc")
                    text = result[0] if isinstance(result, (list, tuple)) else str(result)
                    await websocket.send_json({
                        "text": str(text).strip(),
                        "is_final": False,
                        "bytes_processed": len(buffer),
                    })
                except Exception as err:  # noqa: BLE001
                    await websocket.send_json({"error": str(err)})
    except WebSocketDisconnect:
        LOGGER.info("websocket_asr_client_disconnected")


@app.post("/asr", response_model=ASRResponse)
def asr(request: ASRRequest) -> ASRResponse:
    try:
        payload = base64.b64decode(request.audio_base64, validate=True)
    except Exception as exc:  # noqa: BLE001 - untrusted input boundary
        raise HTTPException(status_code=400, detail=f"audio_base64 is not valid base64: {exc}") from exc
    if not payload:
        raise HTTPException(status_code=400, detail="audio_base64 decoded to zero bytes")

    if STUB_MODE:
        return ASRResponse(
            text=f"[stub transcript: {len(payload)} bytes of {request.audio_format} audio]",
            language=request.language, model=request.model, stub=True,
        )

    waveform = decode_to_pcm16k(payload, request.audio_format)
    if waveform.size == 0:
        return ASRResponse(text="", language=request.language, model=request.model)

    import torch

    model = asr_model(request.model)
    tensor = torch.from_numpy(waveform).unsqueeze(0)
    if next(model.parameters()).is_cuda:
        tensor = tensor.to("cuda")
    with torch.no_grad():
        # IndicConformer exposes both a CTC and an RNNT head. CTC is the faster
        # and more stable of the two for short utterances, which is all this
        # endpoint ever receives.
        result = model(tensor, request.language, "ctc")
    text = result[0] if isinstance(result, (list, tuple)) else str(result)
    return ASRResponse(text=str(text).strip(), language=request.language, model=request.model)


@app.post("/tts", response_model=TTSResponse)
def tts(request: TTSRequest) -> TTSResponse:
    if STUB_MODE:
        import numpy as np

        # A short silence: correct container, correct sample rate, obviously
        # not speech. Enough to prove the audio round trip end to end.
        silence = np.zeros(TARGET_SAMPLE_RATE // 2, dtype=np.float32)
        return TTSResponse(
            audio_base64=base64.b64encode(pcm_to_wav_bytes(silence, TARGET_SAMPLE_RATE)).decode("ascii"),
            sample_rate=TARGET_SAMPLE_RATE, model=request.model, stub=True,
        )

    import torch

    bundle = tts_model(request.model)
    model, device = bundle["model"], bundle["device"]
    language_name = {"kn": "Kannada", "en": "English"}.get(request.language, "Kannada")
    voice = "a female speaker" if request.speaker.lower().startswith("f") else "a male speaker"
    description = (
        f"{voice} speaks {language_name} in a clear, measured, neutral tone. "
        "The recording is very close-sounding and free of background noise."
    )

    description_ids = bundle["description_tokenizer"](description, return_tensors="pt").to(device)
    prompt_ids = bundle["tokenizer"](request.text, return_tensors="pt").to(device)
    with torch.no_grad():
        generation = model.generate(
            input_ids=description_ids.input_ids,
            attention_mask=description_ids.attention_mask,
            prompt_input_ids=prompt_ids.input_ids,
            prompt_attention_mask=prompt_ids.attention_mask,
        )
    audio = generation.cpu().numpy().squeeze()
    sample_rate = int(model.config.sampling_rate)
    return TTSResponse(
        audio_base64=base64.b64encode(pcm_to_wav_bytes(audio, sample_rate)).decode("ascii"),
        sample_rate=sample_rate, model=request.model,
    )


@app.post("/translate", response_model=TranslateResponse)
def translate(request: TranslateRequest) -> TranslateResponse:
    if not request.text or request.source == request.target:
        return TranslateResponse(text=request.text, source=request.source,
                                 target=request.target, model="identity")
    if STUB_MODE:
        return TranslateResponse(text=request.text, source=request.source,
                                 target=request.target, model="stub", stub=True)

    if request.source not in FLORES_CODES or request.target not in FLORES_CODES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language pair {request.source}->{request.target}",
        )

    name = DEFAULT_NMT_EN_INDIC if request.source == "en" else DEFAULT_NMT_INDIC_EN

    import torch

    try:
        from IndicTransToolkit.processor import IndicProcessor
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise HTTPException(
            status_code=503,
            detail=(
                "IndicTransToolkit is not installed, so this service cannot translate. "
                "The platform falls back to its offline Kannada glossary."
            ),
        ) from exc

    processor = _load("nmt:processor", lambda: IndicProcessor(inference=True))
    bundle = nmt_model(name)
    src, tgt = FLORES_CODES[request.source], FLORES_CODES[request.target]
    batch = processor.preprocess_batch([request.text], src_lang=src, tgt_lang=tgt)
    tokenizer, model, device = bundle["tokenizer"], bundle["model"], bundle["device"]
    encoded = tokenizer(batch, truncation=True, padding="longest", return_tensors="pt").to(device)
    with torch.no_grad():
        generated = model.generate(**encoded, num_beams=5, max_length=256)
    decoded = tokenizer.batch_decode(generated, skip_special_tokens=True)
    translated = processor.postprocess_batch(decoded, lang=tgt)[0]
    return TranslateResponse(text=translated, source=request.source,
                             target=request.target, model=name)
