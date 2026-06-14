"""Logger factory; no module-level handler attachment."""
from pathlib import Path
import logging


def get_logger(name: str = "bilibili_podcast") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log_file = Path.cwd() / "log"
    logger.addHandler(logging.FileHandler(log_file))
    logger.addHandler(logging.StreamHandler())
    for h in logger.handlers:
        h.setFormatter(fmt)
    return logger
