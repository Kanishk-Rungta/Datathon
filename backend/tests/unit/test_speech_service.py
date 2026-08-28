"""Unit and contract tests for speech-service/app.py (AI4Bharat Speech Service)."""

from __future__ import annotations

import base64
import sys
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

# Add speech-service directory to sys.path so app can be imported
SPEECH_SERVICE_DIR = Path(__file__).resolve().parents[3] / "speech-service"
if str(SPEECH_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SPEECH_SERVICE_DIR))

import app as speech_app


def test_speech_service_health() -> None:
    client = TestClient(speech_app.app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "defaults" in data
    assert "gpu" in data
    assert "device" in data
    assert "ffmpeg" in data


def test_speech_service_asr_stub_mode(monkeypatch: Any) -> None:
    monkeypatch.setattr(speech_app, "STUB_MODE", True)
    client = TestClient(speech_app.app)

    dummy_audio = base64.b64encode(b"RIFF dummy wav data for testing").decode("ascii")
    response = client.post(
        "/asr",
        json={"audio_base64": dummy_audio, "audio_format": "wav", "language": "kn"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stub"] is True
    assert "[stub transcript:" in data["text"]
    assert data["language"] == "kn"


def test_speech_service_tts_stub_mode(monkeypatch: Any) -> None:
    monkeypatch.setattr(speech_app, "STUB_MODE", True)
    client = TestClient(speech_app.app)

    response = client.post(
        "/tts",
        json={"text": "ನಮಸ್ಕಾರ", "language": "kn", "speaker": "female"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stub"] is True
    assert data["sample_rate"] == 16000
    assert len(data["audio_base64"]) > 0


def test_speech_service_translate_stub_mode(monkeypatch: Any) -> None:
    monkeypatch.setattr(speech_app, "STUB_MODE", True)
    client = TestClient(speech_app.app)

    response = client.post(
        "/translate",
        json={"text": "Hello world", "source": "en", "target": "kn"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["stub"] is True
    assert data["text"] == "Hello world"


def test_speech_service_invalid_base64() -> None:
    client = TestClient(speech_app.app)
    response = client.post(
        "/asr",
        json={"audio_base64": "invalid_base64_!@#$", "audio_format": "wav", "language": "kn"},
    )
    assert response.status_code == 400
