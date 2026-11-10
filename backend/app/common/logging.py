"""日志：每个进程一个 app.log，午夜归档为 app-YYYY-MM-DD.log，保留若干天。"""

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.config import get_settings

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _archive_name(default_name: str) -> str:
    # 默认归档名为 app.log.2026-11-10，改为 app-2026-11-10.log
    directory, filename = os.path.split(default_name)
    date = filename.rsplit(".", 1)[-1]
    return os.path.join(directory, f"app-{date}.log")


def setup_logging(process_name: str) -> None:
    """process_name 为 api 或 worker，各写各的目录，避免两个进程同时归档同一个文件。"""
    settings = get_settings()
    log_dir = Path(settings.LOG_DIR) / process_name
    log_dir.mkdir(parents=True, exist_ok=True)

    handler = TimedRotatingFileHandler(
        log_dir / "app.log", when="midnight", backupCount=settings.LOG_KEEP_DAYS, encoding="utf-8"
    )
    handler.namer = _archive_name
    # 清理过期文件时按新的归档名匹配
    handler.extMatch = re.compile(r"^app-\d{4}-\d{2}-\d{2}\.log$")
    handler.setFormatter(logging.Formatter(_FORMAT))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.LOG_LEVEL)
    root.addHandler(handler)
    root.addHandler(console)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
