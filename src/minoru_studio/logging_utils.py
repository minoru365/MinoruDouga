from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from minoru_studio.redaction import redact_text


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__("%(asctime)s %(levelname)s %(message)s")
        self.secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record), self.secrets)


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
    formatter = RedactingFormatter(secrets)
    for handler in (
        logging.FileHandler(logs_dir / "run.log", encoding="utf-8"),
        logging.StreamHandler(),
    ):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger
