from __future__ import annotations

import http.client
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from .models import VoicevoxProvenance

_HOST = "127.0.0.1"
_PORT = 50021
_TIMEOUT = 10
_JSON_LIMIT = 8 * 1024 * 1024
_WAV_LIMIT = 100 * 1024 * 1024
_SPEAKER_NAME = "ずんだもん"
_STYLE_NAME = "ノーマル"


class VoicevoxUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("VOICEVOX unavailable")


class VoicevoxSynthesisError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("VOICEVOX synthesis")


ConnectionFactory = Callable[[str, int, float], http.client.HTTPConnection]


class VoicevoxClient:
    def __init__(self, *, connection_factory: ConnectionFactory = http.client.HTTPConnection) -> None:
        self._connection_factory = connection_factory

    def preflight(self, *, cancel_event: object | None = None) -> VoicevoxProvenance:
        try:
            version = self._json_request("GET", "/version", None, None, cancel_event, VoicevoxUnavailable)
            if type(version) is not str or not version.strip():
                raise ValueError
            speakers = self._json_request("GET", "/speakers", None, None, cancel_event, VoicevoxUnavailable)
            speaker_id = _resolve_speaker_id(speakers)
            return VoicevoxProvenance(version, _SPEAKER_NAME, _STYLE_NAME, speaker_id)
        except VoicevoxUnavailable:
            raise
        except (TypeError, ValueError):
            raise VoicevoxUnavailable() from None

    def synthesize(self, text: str, speaker_id: int, *, cancel_event: object | None = None) -> bytes:
        try:
            if type(text) is not str or type(speaker_id) is not int or speaker_id <= 0:
                raise ValueError
            speaker = str(speaker_id)
            query = self._json_request(
                "POST",
                f"/audio_query?{urlencode({'text': text, 'speaker': speaker})}",
                None,
                None,
                cancel_event,
                VoicevoxSynthesisError,
            )
            if type(query) is not dict or not query:
                raise ValueError
            query["speedScale"] = 1.0
            payload = json.dumps(query, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            response = self._request(
                "POST",
                f"/synthesis?{urlencode({'speaker': speaker})}",
                payload,
                {"Content-Type": "application/json"},
                cancel_event,
                VoicevoxSynthesisError,
                _WAV_LIMIT,
            )
            content_type = response[0]
            wav = response[1]
            if not wav or not _is_audio_wav(content_type):
                raise ValueError
            return wav
        except VoicevoxSynthesisError:
            raise
        except InterruptedError:
            raise
        except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise VoicevoxSynthesisError() from None

    def _json_request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str] | None,
        cancel_event: object | None,
        error_type: type[VoicevoxUnavailable] | type[VoicevoxSynthesisError],
    ) -> Any:
        _, data = self._request(method, path, body, headers, cancel_event, error_type, _JSON_LIMIT)
        try:
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise error_type() from None

    def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str] | None,
        cancel_event: object | None,
        error_type: type[VoicevoxUnavailable] | type[VoicevoxSynthesisError],
        limit: int,
    ) -> tuple[str | None, bytes]:
        _raise_if_cancelled(cancel_event)
        connection: http.client.HTTPConnection | None = None
        try:
            connection = self._connection_factory(_HOST, _PORT, _TIMEOUT)
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise error_type()
            data = response.read(limit + 1)
            if len(data) > limit:
                raise error_type()
            return response.getheader("Content-Type"), data
        except InterruptedError:
            raise
        except (OSError, http.client.HTTPException, ValueError, TypeError):
            raise error_type() from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except (OSError, http.client.HTTPException):
                    pass


def _raise_if_cancelled(cancel_event: object | None) -> None:
    if cancel_event is not None and bool(getattr(cancel_event, "is_set")()):
        raise InterruptedError()


def _resolve_speaker_id(speakers: Any) -> int:
    if type(speakers) is not list:
        raise ValueError
    matching_speakers = [speaker for speaker in speakers if type(speaker) is dict and speaker.get("name") == _SPEAKER_NAME]
    if len(matching_speakers) != 1:
        raise ValueError
    styles = matching_speakers[0].get("styles")
    if type(styles) is not list:
        raise ValueError
    matching_styles = [style for style in styles if type(style) is dict and style.get("name") == _STYLE_NAME]
    if len(matching_styles) != 1:
        raise ValueError
    speaker_id = matching_styles[0].get("id")
    if type(speaker_id) is not int or speaker_id <= 0:
        raise ValueError
    return speaker_id


def _is_audio_wav(content_type: str | None) -> bool:
    return type(content_type) is str and content_type.split(";", 1)[0].strip().casefold() == "audio/wav"
