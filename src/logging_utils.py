from __future__ import annotations

import logging
from typing import Optional


def setup_logging(level: int = logging.INFO, verbose: bool = False) -> None:
    """Configure root logger with a sensible default format."""
    if verbose:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "zotwatcher")


__all__ = ["setup_logging", "get_logger"]
