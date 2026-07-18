class StateError(ValueError):
    pass


TERMINAL_STATES = frozenset(("applied", "failed"))
ALLOWED = {
    "staging": frozenset(("awaiting_in_out", "checking_still", "failed")),
    "awaiting_in_out": frozenset(("checking_still", "ready", "failed")),
    "checking_still": frozenset(("awaiting_still_setting", "ready", "failed")),
    "awaiting_still_setting": frozenset(("checking_still", "failed")),
    "ready": frozenset(("applying", "failed")),
    "applying": frozenset(("applied", "failed")),
}


def transition(detail, next_state):
    current = detail["state"]
    if current in TERMINAL_STATES:
        raise StateError("terminal application cannot transition")
    if next_state not in ALLOWED.get(current, ()):
        raise StateError(
            "illegal transition: {0} -> {1}".format(current, next_state)
        )
    detail["state"] = next_state
    return detail


def next_action(detail):
    state = detail["state"]
    return {
        "staging": "stage",
        "awaiting_in_out": "resume_in_out",
        "checking_still": "check_still",
        "awaiting_still_setting": "retry_still",
        "ready": "apply",
        "applying": "recover_failed",
        "applied": "new_attempt",
        "failed": "new_attempt",
    }[state]
