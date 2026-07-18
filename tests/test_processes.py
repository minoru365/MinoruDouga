import sys

from minoru_studio.processes import run_process


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
