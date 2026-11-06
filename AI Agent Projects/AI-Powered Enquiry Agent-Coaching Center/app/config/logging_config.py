"""Application logging configuration."""
from __future__ import annotations

import logging
import sys

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logging once with a readable console formatter."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]

    # Quiet down chatty third-party loggers.
    for noisy in ("httpx", "chromadb", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # ChromaDB's bundled posthog telemetry logs a harmless signature error on
    # every call even with telemetry disabled; silence it entirely.
    logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, ensuring logging is configured."""
    setup_logging()
    return logging.getLogger(name)
