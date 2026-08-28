"""Streaming speech-to-text over a WebSocket.

The console records continuously and wants partial transcripts as the officer
speaks, rather than one transcript after they stop. This endpoint provides that
while keeping every existing guarantee: the caller is authenticated, the upload
is bounded, and raw audio is never persisted.

**Why the backend buffers and calls HTTP rather than proxying the speech
service's own WebSocket.** ``speech-service`` exposes ``/ws/stream-asr``, and
forwarding to it would need an outbound WebSocket *client* in the API process.
The only one available is ``websockets``, which arrives transitively through
``uvicorn[standard]`` and is absent from the deployment artifacts' pinned
requirements — the same trap that makes the Bhashini adapter's lazy ``httpx``
import unusable in a deployed function. Buffering here and calling the existing
``LanguageService.transcribe`` instead adds no dependency and reuses an adapter
that is already contract-tested, including its MIME allow-list and byte
ceiling. The speech service's own WebSocket remains available for clients that
can reach it directly.

**Why the token arrives in a message, not the query string.** A browser cannot
set headers on a WebSocket handshake, and a bearer token in a URL is written to
proxy logs, browser history and referrers. The client therefore sends an
``auth`` frame first and the socket is closed if it does not.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ....domain.enums import Language
from ....domain.errors import CIPError
from ....infrastructure.observability import get_logger
from ..deps import get_container_from_request

LOGGER = get_logger(__name__)

router = APIRouter(prefix="/voice", tags=["voice"])

#: Close codes. 1008 is the WebSocket "policy violation" code, which is what a
#: failed authentication is at this layer.
CLOSE_POLICY_VIOLATION = 1008

#: Transcribe once this much new audio has arrived. 32 kB of 16 kHz mono PCM is
#: about one second — often enough to feel live, rarely enough that the speech
#: service is asked to re-transcribe on every frame.
PARTIAL_INTERVAL_BYTES = 32_000


@router.websocket("/stream")
async def stream_asr(websocket: WebSocket) -> None:
    container = get_container_from_request(websocket)  # type: ignore[arg-type]
    await websocket.accept()

    # ------------------------------------------------------------ authenticate
    try:
        opening = await websocket.receive_json()
    except (WebSocketDisconnect, ValueError):
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    token = str(opening.get("token") or "").strip()
    language = str(opening.get("language") or Language.KANNADA.value)
    mime_type = str(opening.get("mime_type") or "audio/webm")
    if not token:
        await websocket.send_json({"error": "An auth frame with a bearer token is required."})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return
    try:
        principal = container.identity_service.principal_from_token(token)
        target = Language(language)
    except (CIPError, ValueError) as exc:
        await websocket.send_json({"error": getattr(exc, "detail", "Authentication failed.")})
        await websocket.close(code=CLOSE_POLICY_VIOLATION)
        return

    if not container.language.is_full_fidelity:
        # Say so rather than streaming silence: the console falls back to the
        # browser's own recogniser when it hears this.
        await websocket.send_json({
            "error": "No server-side speech provider is configured on this deployment.",
            "provider": container.language.provider_name,
            "is_full_fidelity": False,
        })
        await websocket.close()
        return

    await websocket.send_json({
        "ready": True,
        "provider": container.language.provider_name,
        "language": str(target),
    })

    ceiling = container.settings.voice_max_audio_bytes
    buffer = bytearray()
    last_transcribed_at = 0
    transcript = ""

    try:
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            text_frame = message.get("text")
            if text_frame is not None:
                # The client signals end-of-utterance rather than just closing,
                # so a final transcript can be returned before teardown.
                if '"stop"' in text_frame or "stop" == text_frame.strip('"'):
                    transcript = _transcribe(container, bytes(buffer), target, mime_type, transcript)
                    await websocket.send_json({"text": transcript, "is_final": True})
                    break
                continue

            chunk = message.get("bytes")
            if not chunk:
                continue

            buffer.extend(chunk)
            if len(buffer) > ceiling:
                await websocket.send_json({
                    "error": f"Audio exceeded the {ceiling}-byte limit for a single utterance.",
                    "is_final": True,
                })
                break

            if len(buffer) - last_transcribed_at >= PARTIAL_INTERVAL_BYTES:
                last_transcribed_at = len(buffer)
                transcript = _transcribe(container, bytes(buffer), target, mime_type, transcript)
                await websocket.send_json({
                    "text": transcript,
                    "is_final": False,
                    "bytes_received": len(buffer),
                })
    except WebSocketDisconnect:
        pass
    finally:
        # The transcript is the only thing that outlives this call. Raw audio is
        # dropped with the buffer and never written anywhere.
        LOGGER.info(
            "voice_stream_closed",
            extra={"actor": principal.user_id, "bytes": len(buffer)},
        )
        buffer.clear()
        try:
            await websocket.close()
        except RuntimeError:  # pragma: no cover - already closed
            pass


def _transcribe(
    container: Any, audio: bytes, language: Language, mime_type: str, previous: str
) -> str:
    """Transcribe the buffer, keeping the last good result on provider failure.

    A partial transcript that briefly stops updating is a far better failure
    than one that flickers back to empty mid-sentence.
    """
    if not audio:
        return previous
    try:
        return container.language.transcribe(audio, language=language, mime_type=mime_type)
    except CIPError as exc:
        LOGGER.warning("voice_stream_transcribe_failed", extra={"error": str(exc)})
        return previous
