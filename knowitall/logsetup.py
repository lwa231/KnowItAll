"""Rotating file log. Kept free of heavy imports so paths.py can call it before anything else loads."""
import logging
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "knowitall"


def init(log_dir):
    """Log everything to <log_dir>/knowitall.log (1 MB x 4 files). Safe to call more than once."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        return logger
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "knowitall.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
