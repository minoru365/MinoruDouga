import pytest

from resolve_adapter.minoru_studio_resolve.state import (
    StateError,
    next_action,
    transition,
)


def test_waiting_states_resume_and_terminal_states_reject_reuse():
    detail = {"state": "staging", "operation_token": None}
    transition(detail, "awaiting_in_out")
    assert next_action(detail) == "resume_in_out"
    transition(detail, "ready")
    transition(detail, "applying")
    transition(detail, "applied")
    with pytest.raises(StateError, match="terminal"):
        transition(detail, "staging")


def test_cancel_keeps_ready_state():
    assert next_action({"state": "ready", "operation_token": None}) == "apply"
