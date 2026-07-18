import subprocess
import sys
import threading
import time

import pytest

from minoru_studio.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_cancellable_process,
    run_process,
)


def test_run_process_captures_exit_code_and_redacts_display():
    result = run_process(
        [sys.executable, "-c", "import sys; print('ok'); sys.exit(3)", "secret"],
        secrets=["secret"],
    )
    assert result.returncode == 3
    assert result.stdout.strip() == "ok"
    assert "secret" not in result.display_command
    assert "***" in result.display_command


def test_run_process_redacts_whole_secret_shaped_argument_before_escaping():
    result = run_process(
        [sys.executable, "-c", "pass", "--password=alpha beta"],
    )
    assert "alpha beta" not in result.display_command
    assert "beta" not in result.display_command
    assert "***" in result.display_command


def test_run_process_timeout_is_sanitized_without_exception_chaining(monkeypatch):
    secret = "ultra-secret-value"

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            [sys.executable, "--api-key=" + secret],
            timeout=0.01,
            output=secret,
            stderr=secret,
        )

    monkeypatch.setattr("minoru_studio.processes.subprocess.run", timeout)

    with pytest.raises(ProcessTimeoutError) as raised:
        run_process(
            [sys.executable, "--api-key=" + secret], timeout_s=0.01, secrets=[secret]
        )

    error = raised.value
    assert secret not in str(error)
    assert secret not in repr(error)
    assert error.__context__ is None
    assert error.__cause__ is None
    assert error.timeout_s == 0.01
    assert "***" in error.display_command


def test_cancellable_process_terminates_without_exposing_output():
    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        with pytest.raises(ProcessCancelledError) as raised:
            run_cancellable_process(
                [
                    sys.executable,
                    "-c",
                    "import time; print(bytes.fromhex('70726976617465').decode()); time.sleep(30)",
                ],
                cancel_event=cancel,
                poll_interval_s=0.02,
            )
    finally:
        timer.cancel()
    assert "private" not in str(raised.value)


def test_cancellable_process_returns_captured_result():
    result = run_cancellable_process(
        [sys.executable, "-c", "print('ok')"],
        timeout_s=5,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_cancellable_process_already_cancelled_starts_no_child(monkeypatch):
    cancel = threading.Event()
    cancel.set()

    def should_not_start(*args, **kwargs):
        raise AssertionError("Popen must not be called")

    monkeypatch.setattr("minoru_studio.processes.subprocess.Popen", should_not_start)

    with pytest.raises(ProcessCancelledError):
        run_cancellable_process([sys.executable, "-c", "pass"], cancel_event=cancel)


def test_cancellable_process_timeout_terminates_child_and_redacts_secrets(tmp_path):
    secret = "ultra-secret-value"
    marker = tmp_path / "child-survived"
    with pytest.raises(ProcessTimeoutError) as raised:
        run_cancellable_process(
            [
                sys.executable,
                "-c",
                f"import pathlib, time; print(bytes.fromhex('70726976617465').decode()); time.sleep(30); pathlib.Path({str(marker)!r}).touch()",
                f"--api-key={secret}",
            ],
            timeout_s=0.1,
            secrets=[secret],
            poll_interval_s=0.02,
        )
    assert "private" not in str(raised.value)
    assert secret not in str(raised.value)
    assert "***" in raised.value.display_command
    assert not marker.exists()


def test_cancellable_process_rejects_empty_args_before_popen(monkeypatch):
    def should_not_start(*args, **kwargs):
        raise AssertionError("Popen must not be called")

    monkeypatch.setattr("minoru_studio.processes.subprocess.Popen", should_not_start)

    with pytest.raises(ValueError, match="args must not be empty"):
        run_cancellable_process([])


def test_cancellable_process_converts_keyboard_interrupt_and_terminates(monkeypatch):
    class InterruptingProcess:
        def __init__(self):
            self.terminated = False

        def communicate(self, timeout=None):
            if not self.terminated:
                raise KeyboardInterrupt
            return "", ""

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("kill should not be needed")

    process = InterruptingProcess()
    monkeypatch.setattr("minoru_studio.processes.subprocess.Popen", lambda *args, **kwargs: process)

    with pytest.raises(ProcessCancelledError):
        run_cancellable_process([sys.executable, "-c", "pass"])

    assert process.terminated


def test_cancellable_process_cleans_up_interrupt_during_deadline_check(monkeypatch):
    class TrackingProcess:
        def __init__(self):
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("kill should not be needed")

        def communicate(self, timeout=None):
            self.reaped = True
            return "", ""

    process = TrackingProcess()
    monotonic_calls = iter((0.0, KeyboardInterrupt()))
    monkeypatch.setattr("minoru_studio.processes.subprocess.Popen", lambda *args, **kwargs: process)

    def interrupting_monotonic():
        value = next(monotonic_calls)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(
        "minoru_studio.processes.time.monotonic", interrupting_monotonic
    )

    with pytest.raises(ProcessCancelledError):
        run_cancellable_process([sys.executable, "-c", "pass"], timeout_s=5)

    assert process.terminated
    assert process.reaped


def test_cancellable_process_timeout_prevents_delayed_child_side_effect(tmp_path):
    marker = tmp_path / "child-survived"
    with pytest.raises(ProcessTimeoutError):
        run_cancellable_process(
            [
                sys.executable,
                "-c",
                f"import pathlib, time; time.sleep(0.2); pathlib.Path({str(marker)!r}).touch()",
            ],
            timeout_s=0.05,
            poll_interval_s=0.01,
        )

    time.sleep(0.3)
    assert not marker.exists()
