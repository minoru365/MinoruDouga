from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from minoru_studio.redaction import redact_text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        self.secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(str(record.getMessage()), self.secrets)
        record.args = ()
        return True


def configure_job_logger(
    job_dir: Path,
    secrets: Iterable[str] = (),
) -> logging.Logger:
    logs_dir = Path(job_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"minoru_studio.job.{Path(job_dir).resolve()}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    redactor = RedactingFilter(secrets)
    for handler in (
        logging.FileHandler(logs_dir / "run.log", encoding="utf-8"),
        logging.StreamHandler(),
    ):
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
        logger.addHandler(handler)
    return logger
