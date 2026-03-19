"""Workspace manager: initializes workspace from templates on first run."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

_TEMPLATES = Path(__file__).parent / "templates"

# workspace 初始化时跳过这些模板子目录（运行时不使用，避免在 workspace 中留下无用副本）
_SKIP_TEMPLATE_DIRS = {"prompts", "worker"}


class WorkspaceManager:
    """
    管理 ccbot 的主 workspace 目录。

    首次运行时将 templates/ 复制到 workspace（跳过已存在的文件），之后不再覆盖。
    Supervisor 的 Claude Code 子进程以 workspace 为 cwd 启动，会自动加载：
      .claude/CLAUDE.md      — 项目级指令
      .claude/settings.json  — 项目级工具权限
      .claude/skills/        — 可复用技能

    运行时状态统一放在 workspace/.ccbot/ 下：
      .ccbot/memory/         — Supervisor 本地长期/短期记忆
      .ccbot/schedules/      — 定时任务持久化
      .ccbot/dedup/          — 飞书去重缓存
      .ccbot/tmp/            — 飞书临时文件

    其他动态文件：
      output/                — 生成文件输出目录

    Worker 不复用主 workspace；Worker 直接以各自 task.cwd 作为运行目录。
    如果 task.cwd 下没有 .claude/，WorkerPool 会补齐最小模板，但不会覆盖已有配置。
    """

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._init()
        self._migrate_legacy()

    def _init(self) -> None:
        """首次运行：将 templates/ 下的文件复制到 workspace，已存在的跳过。"""
        self.path.mkdir(parents=True, exist_ok=True)
        if not _TEMPLATES.exists():
            return
        for src in _TEMPLATES.rglob("*"):
            if src.is_file():
                rel = src.relative_to(_TEMPLATES)
                # 跳过不需要复制到 workspace 的模板子目录
                if rel.parts and rel.parts[0] in _SKIP_TEMPLATE_DIRS:
                    continue
                dst = self.path / rel
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    logger.debug("初始化: {}", rel)

    def _migrate_legacy(self) -> None:
        """迁移旧版 ~/.ccbot/ 下散落的运行时目录到 workspace/.ccbot/，完成后删除源目录。"""
        legacy_root = Path.home() / ".ccbot"
        for dirname in ("dedup", "tmp"):
            src = legacy_root / dirname
            if not src.is_dir():
                continue
            dst = self.runtime_dir / dirname
            if not dst.exists():
                try:
                    shutil.copytree(src, dst)
                    logger.info("迁移旧目录: {} → {}", src, dst)
                except OSError as exc:
                    logger.warning("迁移旧目录失败: {} → {} ({})", src, dst, exc)
                    continue
            # 目标已存在（迁移完成或之前已迁移），删除旧目录
            try:
                shutil.rmtree(src)
                logger.info("已删除旧版目录: {}", src)
            except OSError as exc:
                logger.warning("删除旧版目录失败: {} ({})", src, exc)

    @property
    def output_dir(self) -> Path:
        """Claude 写出文件的目录，由 Channel 自动上传或展示。"""
        return self.path / "output"

    @property
    def runtime_dir(self) -> Path:
        return self.path / ".ccbot"

    @property
    def dedup_dir(self) -> Path:
        """飞书去重缓存目录。"""
        return self.runtime_dir / "dedup"

    @property
    def tmp_dir(self) -> Path:
        """飞书临时文件目录。"""
        return self.runtime_dir / "tmp"

    def build_system_prompt(self) -> str:
        """注入静态内容：workspace 路径 + 当前日期。日期天级精度，KV cache 友好。"""
        from datetime import datetime

        current_date = datetime.now().strftime("%Y-%m-%d (%A)")
        return f"Workspace: {self.path}\nCurrent date: {current_date}"
