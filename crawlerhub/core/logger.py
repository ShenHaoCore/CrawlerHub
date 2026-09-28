"""日志模块：基于 loguru 的统一日志配置。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from loguru import logger


def setup_logger(
    level: str = "INFO",
    log_file: str | None = "logs/crawlerhub.log",
    rotation: str = "10 MB",
    retention: str = "7 days",
) -> Any:
    """初始化全局日志配置。

    Args:
        level: 日志级别，如 DEBUG/INFO/WARNING/ERROR。
        log_file: 日志文件路径，为 None 时仅输出到控制台。
        rotation: 日志文件轮转大小。
        retention: 日志保留时长。

    Returns:
        配置后的 loguru logger 对象。
    """
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )

    # 文件输出
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_path),
            level=level,
            rotation=rotation,
            retention=retention,
            encoding="utf-8",
            enqueue=True,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        )

    return logger


__all__ = ["logger", "setup_logger"]
