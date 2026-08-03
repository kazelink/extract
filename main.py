from __future__ import annotations

import logging
import logging.handlers
import sys

from core.config import LOG_FILE_PATH
from ui.app import run


def _setup_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(
            logging.handlers.RotatingFileHandler(
                LOG_FILE_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
            )
        )
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    for noisy_logger in ("httpx", "openai"):
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)


if __name__ == "__main__":
    _setup_logging()
    run()
