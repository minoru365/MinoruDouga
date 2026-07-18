import logging

from minoru_studio.logging_utils import configure_job_logger
from minoru_studio.redaction import redact_text


def test_redact_text_masks_values_and_secret_shaped_arguments():
    text = "OPENAI_API_KEY=abc123 --api-key=abc123 token=other"
    redacted = redact_text(text, secrets=["abc123", "other"])
    assert "abc123" not in redacted
    assert "other" not in redacted
    assert redacted.count("***") >= 3


def test_job_logger_never_writes_known_secret(tmp_path):
    logger = configure_job_logger(tmp_path, secrets=["top-secret"])
    logger.info("credential=top-secret")
    for handler in logger.handlers:
        handler.flush()
    text = (tmp_path / "logs" / "run.log").read_text(encoding="utf-8")
    assert "top-secret" not in text
    assert "***" in text
