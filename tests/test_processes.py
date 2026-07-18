import subprocess
import sys

import pytest

from minoru_studio.processes import ProcessTimeoutError, run_process


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
