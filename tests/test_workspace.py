"""Tests for WorkspaceManager."""

from pathlib import Path

import pytest

from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


def test_workspace_dir_created_on_init(ws: WorkspaceManager) -> None:
    assert ws.path.is_dir()


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
    assert "<runtime_metadata>" in prompt
    assert str(ws.path) in prompt


def test_build_system_prompt_includes_date(ws: WorkspaceManager) -> None:
    """日期应在 system prompt 中（天级精度，KV cache 友好）。"""
    import re

    prompt = ws.build_system_prompt()
    assert re.search(r"\d{4}-\d{2}-\d{2}", prompt), "system prompt should include current date"


def test_build_system_prompt_is_concise(ws: WorkspaceManager) -> None:
    # 新的 build_system_prompt 只注入动态内容，不包含大段静态指令
    prompt = ws.build_system_prompt()
    assert len(prompt) < 500, "system prompt should be concise (static content is in CLAUDE.md)"


def test_dedup_dir_and_tmp_dir(ws: WorkspaceManager) -> None:
    """dedup_dir 和 tmp_dir 应指向 workspace 根目录下。"""
    assert ws.dedup_dir == ws.path / "dedup"
    assert ws.tmp_dir == ws.path / "tmp"


def test_runtime_dir_is_workspace_root(ws: WorkspaceManager) -> None:
    """runtime_dir 应等于 workspace 根目录。"""
    assert ws.runtime_dir == ws.path


def test_skip_template_dirs(tmp_path: Path) -> None:
    """prompts/ 和 worker/ 模板子目录不应被复制到 workspace。"""
    ws = WorkspaceManager(tmp_path / "ws_skip")
    # 即使 templates/prompts 和 templates/worker 存在，workspace 中也不应有
    assert not (ws.path / "prompts").exists(), "prompts/ should not be copied to workspace"
    assert not (ws.path / "worker").exists(), "worker/ should not be copied to workspace"
