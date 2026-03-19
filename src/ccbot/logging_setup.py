"""日志配置模块。"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger


def setup_logging(config: "LoggingConfig") -> None:
    """根据配置初始化日志系统。"""
    from ccbot.config import LoggingConfig

    # 移除默认处理器
    logger.remove()

    level = config.level

    # 控制台输出
    if config.console_enabled:
        console_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        )
        logger.add(sys.stderr, level=level, format=console_format)

    # 文件输出
    if config.file_enabled:
        file_path = Path(config.file_path).expanduser()
        file_path.parent.mkdir(parents=True, exist_ok=True)

        if config.format == "json":
            file_format = (
                '{{"time": "{time:YYYY-MM-DD HH:mm:ss}", '
                '"level": "{level}", '
                '"name": "{name}", '
                '"function": "{function}", '
                '"line": {line}, '
                '"message": "{message}"}}'
            )
        else:
            file_format = (
                "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
            )

        logger.add(
            file_path,
            level=level,
            format=file_format,
            rotation=config.max_file_size_mb * 1024 * 1024,
            retention=f"{config.rotation_days} days",
            compression="zip",
        )
