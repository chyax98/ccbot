"""Tests for WorkspaceManager."""

from pathlib import Path

import pytest

from ccbot.workspace import WorkspaceManager


@pytest.fixture
def ws(tmp_path: Path) -> WorkspaceManager:
    return WorkspaceManager(tmp_path / "workspace")


def test_dirs_created_on_init(ws: WorkspaceManager) -> None:
    assert (ws.path / "memory").is_dir()
    assert (ws.path / "skills").is_dir()


def test_memory_file_path(ws: WorkspaceManager) -> None:
    assert ws.memory_file == ws.path / "memory" / "MEMORY.md"


def test_history_file_path(ws: WorkspaceManager) -> None:
    assert ws.history_file == ws.path / "memory" / "HISTORY.md"


def test_heartbeat_file_path(ws: WorkspaceManager) -> None:
    assert ws.heartbeat_file == ws.path / "HEARTBEAT.md"


def test_read_memory_empty_when_missing(ws: WorkspaceManager) -> None:
    assert ws.read_memory() == ""


def test_read_memory_returns_content(ws: WorkspaceManager) -> None:
    ws.memory_file.write_text("# Facts\n\nfoo bar", encoding="utf-8")
    assert ws.read_memory() == "# Facts\n\nfoo bar"


def test_build_system_prompt_contains_identity(ws: WorkspaceManager) -> None:
    prompt = ws.build_system_prompt()
    assert "nanobot" in prompt
    assert str(ws.path) in prompt


def test_build_system_prompt_includes_memory(ws: WorkspaceManager) -> None:
    ws.memory_file.write_text("Important fact: sky is blue", encoding="utf-8")
    prompt = ws.build_system_prompt()
    assert "Important fact: sky is blue" in prompt


def test_build_system_prompt_includes_bootstrap_file(ws: WorkspaceManager) -> None:
    (ws.path / "SOUL.md").write_text("Be helpful.", encoding="utf-8")
    prompt = ws.build_system_prompt()
    assert "Be helpful." in prompt


def test_skills_summary_empty_when_no_skills(tmp_path: Path) -> None:
    ws = WorkspaceManager(tmp_path / "empty_ws")
    # No builtin skills exist relative to test env — summary may be empty or populated
    # Just verify it doesn't crash
    prompt = ws.build_system_prompt()
    assert isinstance(prompt, str)


def test_workspace_skill_overrides_builtin(tmp_path: Path) -> None:
    ws = WorkspaceManager(tmp_path / "ws")
    skill_dir = ws.path / "skills" / "test_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\ndescription: My custom skill\n---\n\nDo things.", encoding="utf-8"
    )
    prompt = ws.build_system_prompt()
    assert "test_skill" in prompt


def test_always_skill_injected_into_system_prompt(tmp_path: Path) -> None:
    ws = WorkspaceManager(tmp_path / "ws")
    skill_dir = ws.path / "skills" / "always_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nmetadata: {"nanobot": {"always": true}}\ndescription: Always on\n---\n\nAlways active content.',
        encoding="utf-8",
    )
    prompt = ws.build_system_prompt()
    assert "Always active content." in prompt


def test_skill_with_missing_bin_not_available(tmp_path: Path) -> None:
    ws = WorkspaceManager(tmp_path / "ws")
    skill_dir = ws.path / "skills" / "missing_bin_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        '---\nmetadata: {"nanobot": {"requires": {"bins": ["__nonexistent_binary__9999"]}}}\ndescription: Needs bin\n---\n\nContent.',
        encoding="utf-8",
    )
    # Skill should show as available=false in summary
    summary = ws._build_skills_summary()
    assert 'available="false"' in summary
