from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from minoru_studio.narrate.voicevox import (
    VoicevoxClient,
    VoicevoxSynthesisError,
    VoicevoxUnavailable,
)


@dataclass
class FakeResponse:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] | None = None
    read_amount: int | None = None

    def read(self, amount: int = -1) -> bytes:
        self.read_amount = amount
        return self.body if amount < 0 else self.body[:amount]

    def getheader(self, name: str, default: str | None = None) -> str | None:
        return (self.headers or {}).get(name, default)


class FakeConnection:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str] | None]] = []
        self.closed = False

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


class ConnectionFactory:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = iter(responses)
        self.calls: list[tuple[str, int, float]] = []
        self.connections: list[FakeConnection] = []

    def __call__(self, host: str, port: int, timeout: float) -> FakeConnection:
        self.calls.append((host, port, timeout))
        connection = FakeConnection(next(self.responses))
        self.connections.append(connection)
        return connection


class StrictHeaderConnection(FakeConnection):
    def request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> None:
        # http.client._send_request は headers を無条件に反復するため None を受け付けない
        frozenset(name.lower() for name in headers)
        super().request(method, path, body, headers)


class StrictHeaderConnectionFactory(ConnectionFactory):
    def __call__(self, host: str, port: int, timeout: float) -> FakeConnection:
        self.calls.append((host, port, timeout))
        connection = StrictHeaderConnection(next(self.responses))
        self.connections.append(connection)
        return connection


def speakers(style_id: object = 2, *, duplicate_speaker: bool = False, duplicate_style: bool = False) -> bytes:
    styles = [{"name": "ノーマル", "id": style_id}]
    if duplicate_style:
        styles.append({"name": "ノーマル", "id": 3})
    result: list[dict[str, Any]] = [{"name": "ずんだもん", "styles": styles}]
    if duplicate_speaker:
        result.append({"name": "ずんだもん", "styles": [{"name": "ノーマル", "id": 4}]})
    return json.dumps(result).encode()


def client_with(*responses: FakeResponse) -> tuple[VoicevoxClient, ConnectionFactory]:
    factory = ConnectionFactory(list(responses))
    return VoicevoxClient(connection_factory=factory), factory


def test_preflight_resolves_exact_names_and_uses_literal_loopback_origin():
    client, factory = client_with(FakeResponse(body=b'"0.25.2"'), FakeResponse(body=speakers()))

    provenance = client.preflight()

    assert provenance.engine_version == "0.25.2"
    assert provenance.speaker_name == "ずんだもん"
    assert provenance.style_name == "ノーマル"
    assert provenance.speaker_id == 2
    assert factory.calls == [("127.0.0.1", 50021, 10), ("127.0.0.1", 50021, 10)]
    assert [connection.requests[0][:2] for connection in factory.connections] == [("GET", "/version"), ("GET", "/speakers")]
    assert all(connection.closed for connection in factory.connections)


def test_requests_send_headers_compatible_with_real_http_client():
    factory = StrictHeaderConnectionFactory(
        [FakeResponse(body=b'"0.25.2"'), FakeResponse(body=speakers())]
    )
    client = VoicevoxClient(connection_factory=factory)

    provenance = client.preflight()

    assert provenance.engine_version == "0.25.2"
    assert provenance.speaker_id == 2


def test_synthesize_url_encodes_text_and_replaces_only_speed_scale():
    text = "a & 日本語?"
    query = {"speedScale": 0.5, "pitchScale": 0.25, "nested": {"keep": True}}
    client, factory = client_with(
        FakeResponse(body=json.dumps(query).encode()),
        FakeResponse(body=b"RIFF wav", headers={"Content-Type": "audio/wav"}),
    )

    result = client.synthesize(text, 2)

    assert result == b"RIFF wav"
    audio_query = factory.connections[0].requests[0]
    synthesis = factory.connections[1].requests[0]
    assert audio_query[0] == "POST"
    parsed = urlsplit(audio_query[1])
    assert parsed.path == "/audio_query"
    assert parse_qs(parsed.query) == {"text": [text], "speaker": ["2"]}
    assert synthesis[0] == "POST"
    assert urlsplit(synthesis[1]).path == "/synthesis"
    assert parse_qs(urlsplit(synthesis[1]).query) == {"speaker": ["2"]}
    assert json.loads(synthesis[2] or b"") == {**query, "speedScale": 1.0}
    assert synthesis[3] == {"Content-Type": "application/json"}
    assert all(connection.closed for connection in factory.connections)


@pytest.mark.parametrize("cancel_on", ["version", "speakers", "audio_query", "synthesis"])
def test_cancellation_is_checked_before_every_request(cancel_on: str):
    class CancelEvent:
        calls = 0

        def is_set(self) -> bool:
            self.calls += 1
            return self.calls == {"version": 1, "speakers": 2, "audio_query": 1, "synthesis": 2}[cancel_on]

    event = CancelEvent()
    responses = (
        (FakeResponse(body=b'"0.25.2"'), FakeResponse(body=speakers()))
        if cancel_on in {"version", "speakers"}
        else (FakeResponse(body=b'{"speedScale": 0.5}'), FakeResponse(body=b"RIFF", headers={"Content-Type": "audio/wav"}))
    )
    client, factory = client_with(*responses)

    with pytest.raises(InterruptedError):
        if cancel_on in {"version", "speakers"}:
            client.preflight(cancel_event=event)
        else:
            client.synthesize("private utterance", 2, cancel_event=event)

    assert len(factory.connections) == {"version": 0, "speakers": 1, "audio_query": 0, "synthesis": 1}[cancel_on]


@pytest.mark.parametrize("status", [199, 300, 302, 400, 500])
def test_preflight_rejects_every_non_2xx_status(status: int):
    client, _ = client_with(FakeResponse(status=status, body=b"sensitive body"))

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()


@pytest.mark.parametrize("body", [b"not json", b"{}", b"[]", b'{"name": "wrong"}', b'""'])
def test_preflight_rejects_malformed_or_invalid_version(body: bytes):
    client, _ = client_with(FakeResponse(body=body))

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()


@pytest.mark.parametrize("style_id", [True, 0, -1, 1.5, "2"])
def test_preflight_rejects_non_positive_or_non_integer_style_id(style_id: object):
    client, _ = client_with(FakeResponse(body=b'"0.25.2"'), FakeResponse(body=speakers(style_id)))

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()


@pytest.mark.parametrize("kwargs", [{"duplicate_speaker": True}, {"duplicate_style": True}])
def test_preflight_rejects_ambiguous_exact_names(kwargs: dict[str, bool]):
    client, _ = client_with(FakeResponse(body=b'"0.25.2"'), FakeResponse(body=speakers(**kwargs)))

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()


def test_preflight_rejects_oversized_json_without_exposing_body():
    response = FakeResponse(body=b'"' + b"x" * (8 * 1024 * 1024) + b'"')
    client, _ = client_with(response)

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()

    assert response.read_amount == 8 * 1024 * 1024 + 1


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_preflight_rejects_non_rfc_json_constants(constant: bytes):
    client, _ = client_with(
        FakeResponse(body=b'"0.25.2"'),
        FakeResponse(body='[{"name":"ずんだもん","styles":[{"name":"ノーマル","id":2}],"extra":'.encode() + constant + b"}]"),
    )

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_synthesize_rejects_non_rfc_json_constants(constant: bytes):
    client, _ = client_with(
        FakeResponse(body=b'{"speedScale":' + constant + b"}"),
        FakeResponse(body=b"RIFF", headers={"Content-Type": "audio/wav"}),
    )

    with pytest.raises(VoicevoxSynthesisError, match=r"^VOICEVOX synthesis$"):
        client.synthesize("private utterance", 2)


@pytest.mark.parametrize(
    "speaker_body",
    [b"[]", json.dumps([{"name": "other", "styles": []}]).encode(), json.dumps([{"name": "ずんだもん", "styles": []}]).encode()],
)
def test_preflight_rejects_missing_exact_speaker_or_style(speaker_body: bytes):
    client, _ = client_with(FakeResponse(body=b'"0.25.2"'), FakeResponse(body=speaker_body))

    with pytest.raises(VoicevoxUnavailable, match=r"^VOICEVOX unavailable$"):
        client.preflight()


@pytest.mark.parametrize(
    "query_response",
    [FakeResponse(status=500, body=b"secret"), FakeResponse(body=b"not-json"), FakeResponse(body=b"{}")],
)
def test_synthesize_rejects_non_2xx_malformed_or_empty_query(query_response: FakeResponse):
    client, _ = client_with(query_response)

    with pytest.raises(VoicevoxSynthesisError, match=r"^VOICEVOX synthesis$"):
        client.synthesize("private utterance", 2)


@pytest.mark.parametrize(
    "response",
    [FakeResponse(status=302, body=b"secret"), FakeResponse(body=b""), FakeResponse(body=b"RIFF", headers={"Content-Type": "application/json"})],
)
def test_synthesize_rejects_invalid_wav_response(response: FakeResponse):
    client, _ = client_with(FakeResponse(body=b'{"speedScale": 0.5}'), response)

    with pytest.raises(VoicevoxSynthesisError, match=r"^VOICEVOX synthesis$"):
        client.synthesize("private utterance", 2)


def test_synthesize_rejects_oversized_wav_and_never_leaks_utterance():
    utterance = "do not expose this utterance"
    response = FakeResponse(body=b"R" * (100 * 1024 * 1024 + 1), headers={"Content-Type": "audio/wav"})
    client, _ = client_with(
        FakeResponse(body=b'{"speedScale": 0.5}'),
        response,
    )

    with pytest.raises(VoicevoxSynthesisError) as caught:
        client.synthesize(utterance, 2)

    assert str(caught.value) == "VOICEVOX synthesis"
    assert utterance not in str(caught.value)
    assert response.read_amount == 100 * 1024 * 1024 + 1
