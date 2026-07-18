from __future__ import annotations

import re
from collections.abc import Iterable


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|auth|password|secret)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s]+)"
)


def redact_text(value: str, secrets: Iterable[str] = ()) -> str:
    redacted = value
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        redacted = redacted.replace(secret, "***")
    return _SECRET_ASSIGNMENT.sub(r"\1\2***", redacted)
