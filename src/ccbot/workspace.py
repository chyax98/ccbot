"""Workspace manager: initializes workspace from templates on first run."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger

_TEMPLATES = Path(__file__).parent / "templates"


class WorkspaceManager:
    """
    管理 ccbot workspace 目录。

    首次运行时将 templates/ 复制到 workspace（跳过已存在的文件），之后不再覆盖。
    Claude Code 子进程以 workspace 为 cwd 启动，自动加载：
      .claude/CLAUDE.md      — 项目级指令（个性、工具说明、heartbeat 说明）
      .claude/settings.json  — 工具权限（disallowedTools 等）
      .claude/skills/        — 技能（原生 Skill tool 管理）
      native auto memory     — ~/.claude/projects/<hash>/memory/

    workspace 下只有一个需要关注的动态文件：
      HEARTBEAT.md           — 定时任务列表
    """

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self._init()

    def _init(self) -> None:
        """首次运行：将 templates/ 下的文件复制到 workspace，已存在的跳过。"""
        self.path.mkdir(parents=True, exist_ok=True)
        if not _TEMPLATES.exists():
            return
        for src in _TEMPLATES.rglob("*"):
            if src.is_file():
                rel = src.relative_to(_TEMPLATES)
                dst = self.path / rel
                if not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    logger.debug("初始化: {}", rel)

    @property
    def heartbeat_file(self) -> Path:
        return self.path / "HEARTBEAT.md"

    @property
    def output_dir(self) -> Path:
        """Claude 写出文件的目录，由 FeishuChannel 自动上传。"""
        return self.path / "output"

    def build_system_prompt(self) -> str:
        """注入动态内容：workspace 路径 + 当前时间。

        静态指令（个性、工具说明、技能）由 .claude/CLAUDE.md 承载，Claude Code 自动加载。
        Memory 由 Claude Code 原生 auto memory 管理，无需手动注入。
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        return f"Workspace: {self.path}\nCurrent time: {now}"
