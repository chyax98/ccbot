"""Tests for WorkspaceManager."""

from pathlib import Path

import pytest

from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


def test_workspace_dir_created_on_init(ws: WorkspaceManager) -> None:
    assert ws.path.is_dir()


def test_heartbeat_file_path(ws: WorkspaceManager) -> None:
    assert ws.heartbeat_file == ws.path / "HEARTBEAT.md"


def test_templates_copied_on_init(ws: WorkspaceManager) -> None:
    # templates/.claude/settings.json 应已复制
    settings = ws.path / ".claude" / "settings.json"
    assert settings.exists(), ".claude/settings.json should be copied from templates"


def test_templates_not_overwritten(ws: WorkspaceManager) -> None:
    # 已存在的文件不应被覆盖
    settings = ws.path / ".claude" / "settings.json"
    if settings.exists():
        settings.write_text("custom content")
        # 重新初始化
        WorkspaceManager(ws.path)
        assert settings.read_text() == "custom content", "existing files should not be overwritten"


def test_build_system_prompt_contains_workspace_path(ws: WorkspaceManager) -> None:
    prompt = ws.build_system_prompt()
    assert str(ws.path) in prompt


def test_build_system_prompt_no_time(ws: WorkspaceManager) -> None:
    """时间改由 team.py 每轮注入 runtime_context，不应出现在 system prompt 中。"""
    import re

    prompt = ws.build_system_prompt()
    assert not re.search(r"\d{4}-\d{2}-\d{2}", prompt), (
        "time should not be in system prompt (injected per-turn in runtime_context)"
    )


def test_build_system_prompt_is_concise(ws: WorkspaceManager) -> None:
    # 新的 build_system_prompt 只注入动态内容，不包含大段静态指令
    prompt = ws.build_system_prompt()
    assert len(prompt) < 500, "system prompt should be concise (static content is in CLAUDE.md)"
