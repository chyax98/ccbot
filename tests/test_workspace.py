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
    """dedup_dir 和 tmp_dir 应指向 workspace/.ccbot/ 下。"""
    assert ws.dedup_dir == ws.path / ".ccbot" / "dedup"
    assert ws.tmp_dir == ws.path / ".ccbot" / "tmp"


def test_skip_template_dirs(tmp_path: Path) -> None:
    """prompts/ 和 worker/ 模板子目录不应被复制到 workspace。"""
    ws = WorkspaceManager(tmp_path / "ws_skip")
    # 即使 templates/prompts 和 templates/worker 存在，workspace 中也不应有
    assert not (ws.path / "prompts").exists(), "prompts/ should not be copied to workspace"
    assert not (ws.path / "worker").exists(), "worker/ should not be copied to workspace"


def test_migrate_legacy(tmp_path: Path) -> None:
    """_migrate_legacy 应将旧目录迁移到 workspace/.ccbot/ 并删除源目录。"""
    from unittest.mock import patch

    fake_home = tmp_path / "fakehome"
    old_dedup = fake_home / ".ccbot" / "dedup"
    old_dedup.mkdir(parents=True)
    (old_dedup / "feishu.json").write_text('{"ids": []}')

    with patch("ccbot.workspace.Path.home", return_value=fake_home):
        ws = WorkspaceManager(tmp_path / "ws_migrate")

    # 数据已迁移到新位置
    new_dedup = ws.runtime_dir / "dedup"
    assert new_dedup.is_dir()
    assert (new_dedup / "feishu.json").exists()
    # 旧目录已删除
    assert not old_dedup.exists()


def test_migrate_legacy_idempotent(tmp_path: Path) -> None:
    """目标已存在时不覆盖数据，但仍删除旧目录。"""
    from unittest.mock import patch

    fake_home = tmp_path / "fakehome"
    old_dedup = fake_home / ".ccbot" / "dedup"
    old_dedup.mkdir(parents=True)
    (old_dedup / "feishu.json").write_text("old")

    with patch("ccbot.workspace.Path.home", return_value=fake_home):
        ws = WorkspaceManager(tmp_path / "ws_idem")
        # 修改目标内容
        (ws.runtime_dir / "dedup" / "feishu.json").write_text("new")
        # 重建旧目录模拟残留
        old_dedup.mkdir(parents=True, exist_ok=True)
        (old_dedup / "feishu.json").write_text("stale")
        # 再次初始化
        WorkspaceManager(ws.path)

    # 目标数据未被覆盖
    assert (ws.runtime_dir / "dedup" / "feishu.json").read_text() == "new"
    # 旧目录已删除
    assert not old_dedup.exists()
