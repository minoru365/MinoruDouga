from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from minoru_studio.transcribe.contracts import (
    ContractError,
    SegmentResult,
    WordResult,
    WorkerRequest,
    WorkerResult,
    load_worker_request,
    load_worker_result,
    save_worker_request,
    save_worker_result,
)


def valid_request(tmp_path: Path) -> WorkerRequest:
    return WorkerRequest(
        schema_version=1,
        input_wav=str(tmp_path / "inference.wav"),
        output_json=str(tmp_path / "raw-segments.json"),
        model="small",
        language="ja",
        device="cpu",
        compute_type="int8",
        vad_filter=True,
        word_timestamps=True,
        model_cache_dir=str(tmp_path / "models"),
        allow_model_download=False,
    )


def valid_result_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "model": "small",
        "provider_version": "1.2.1",
        "language": "ja",
        "language_probability": 0.99,
        "duration_ms": 2_000,
        "duration_after_vad_ms": 1_600,
        "no_speech": False,
        "segments": [
            {
                "start_ms": 100,
                "end_ms": 800,
                "text": "こんにちは。",
                "words": [{"start_ms": 100, "end_ms": 800, "text": "こんにちは。"}],
            }
        ],
    }


def test_worker_request_round_trips_with_absolute_paths(tmp_path):
    request = valid_request(tmp_path)
    path = tmp_path / "worker-request.json"

    save_worker_request(path, request)

    assert load_worker_request(path) == request
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_worker_request_rejects_relative_paths_and_unknown_runtime_values(tmp_path):
    request = valid_request(tmp_path)
    path = tmp_path / "worker-request.json"

    with pytest.raises(ContractError, match="absolute"):
        save_worker_request(path, replace(request, input_wav="input.wav"))

    payload = {
        **asdict(request),
        "model": "large-v3",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ContractError, match="model"):
        load_worker_request(path)


def test_worker_result_round_trip_requires_integer_monotonic_times(tmp_path):
    result = WorkerResult(
        schema_version=1,
        model="small",
        provider_version="1.2.1",
        language="ja",
        language_probability=0.99,
        duration_ms=2_000,
        duration_after_vad_ms=1_600,
        no_speech=False,
        segments=(
            SegmentResult(
                start_ms=100,
                end_ms=800,
                text="こんにちは。",
                words=(WordResult(100, 800, "こんにちは。"),),
            ),
        ),
    )
    path = tmp_path / "raw-segments.json"

    save_worker_result(path, result)

    assert load_worker_result(path) == result


@pytest.mark.parametrize("bad", [True, 1.5, -1])
def test_worker_result_rejects_invalid_milliseconds(tmp_path, bad):
    payload = valid_result_payload()
    payload["segments"][0]["start_ms"] = bad
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError):
        load_worker_result(path)


def test_worker_result_rejects_out_of_order_or_overlapping_times(tmp_path):
    payload = valid_result_payload()
    payload["segments"].append(
        {"start_ms": 700, "end_ms": 900, "text": "重複", "words": []}
    )
    path = tmp_path / "bad-order.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError, match="ordered"):
        load_worker_result(path)


@pytest.mark.parametrize("schema_version", [0, 2, True])
def test_worker_contracts_reject_unknown_schema_versions(tmp_path, schema_version):
    payload = valid_result_payload()
    payload["schema_version"] = schema_version
    path = tmp_path / "unknown-schema.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError, match="schema"):
        load_worker_result(path)


def test_no_speech_result_cannot_contain_segments(tmp_path):
    payload = valid_result_payload()
    payload["no_speech"] = True
    path = tmp_path / "no-speech.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ContractError, match="no_speech"):
        load_worker_result(path)
