"""The streaming dictation WebSocket.

Two properties matter more than the transcript itself:

* **the socket authenticates before it streams**, using a frame rather than a
  query parameter, because a bearer token in a URL is written to proxy logs and
  browser history; and
* **an absent provider is reported, not faked** — the console falls back to the
  browser's own recogniser when it hears that, so a wrong answer here silently
  removes the officer's microphone.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow


def token_for(client, username="analyst.state"):
    return client.post("/api/v1/auth/login",
                       json={"username": username, "password": "ChangeMe#2026"}).json()["access_token"]


class TestAuthentication:
    def test_a_socket_without_a_token_is_refused(self, client):
        with client.websocket_connect("/api/v1/voice/stream") as socket:
            socket.send_json({})
            assert "error" in socket.receive_json()

    def test_a_malformed_token_is_refused(self, client):
        with client.websocket_connect("/api/v1/voice/stream") as socket:
            socket.send_json({"token": "not-a-real-token"})
            assert "error" in socket.receive_json()

    def test_the_token_is_never_taken_from_the_query_string(self, client, tokens):
        """Accepting it in the URL would put a credential in every proxy log."""
        raw = tokens["analyst"]["Authorization"].split(" ", 1)[1]
        with client.websocket_connect(f"/api/v1/voice/stream?token={raw}") as socket:
            socket.send_json({})
            assert "error" in socket.receive_json(), "a query-string token must not authenticate"


class TestProviderReporting:
    def test_an_absent_provider_is_reported_rather_than_streaming_silence(self, client):
        """The local build has no ASR provider, so it must say so."""
        with client.websocket_connect("/api/v1/voice/stream") as socket:
            socket.send_json({"token": token_for(client), "language": "kn"})
            message = socket.receive_json()
            assert message.get("is_full_fidelity") is False
            assert "error" in message
