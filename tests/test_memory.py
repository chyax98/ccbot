from __future__ import annotations

from pathlib import Path

from ccbot.memory import MemoryStore


def test_memory_store_bootstraps_long_term_file(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace")
    assert store.long_term_file.exists()
    assert "长期记忆" in store.long_term_file.read_text(encoding="utf-8")


def test_memory_store_persists_runtime_session_and_turns(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace")
    store.set_runtime_session("chat-1", "sess-123")
    store.remember_turn("chat-1", "你好", "您好")

    memory = store.load("chat-1")
    assert memory.runtime_session_id == "sess-123"
    assert [turn.role for turn in memory.short_term] == ["user", "assistant"]
    prompt = store.build_memory_prompt("chat-1")
    assert "sess-123" not in prompt
    assert "短期记忆" in prompt
    assert "你好" in prompt


def test_memory_store_clear_conversation(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace")
    store.set_runtime_session("chat-1", "sess-123")
    assert store.conversation_file("chat-1").exists()
    store.clear_conversation("chat-1")
    assert not store.conversation_file("chat-1").exists()


def test_remember_turn_strips_runtime_context(tmp_path: Path) -> None:
    """remember_turn 存入前应过滤 <runtime_context> 块，避免系统状态污染短期记忆。"""
    store = MemoryStore(tmp_path / "workspace")
    enhanced = "<runtime_context>\nCurrent date: 2026-03-09\n</runtime_context>\n\n用户真实消息"
    store.remember_turn("chat-1", enhanced, "回复")

    memory = store.load("chat-1")
    assert memory.short_term
    user_turn = next(t for t in memory.short_term if t.role == "user")
    assert "runtime_context" not in user_turn.content
    assert "用户真实消息" in user_turn.content


def test_build_long_term_prompt_skips_short_term(tmp_path: Path) -> None:
    """build_long_term_prompt 仅注入长期记忆，不含短期对话（resume 时避免重复）。"""
    store = MemoryStore(tmp_path / "workspace")
    store.long_term_file.write_text("# 长期记忆\n偏好: Python", encoding="utf-8")
    store.remember_turn("chat-1", "你好", "您好")

    prompt = store.build_long_term_prompt("chat-1")
    assert "长期记忆" in prompt
    assert "Python" in prompt
    assert "短期记忆" not in prompt
    assert "你好" not in prompt


def test_build_long_term_prompt_empty_when_no_long_term(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace")
    # 清空长期记忆文件使其为空
    store.long_term_file.write_text("", encoding="utf-8")
    prompt = store.build_long_term_prompt("chat-1")
    assert prompt == ""


def test_memory_turn_created_at_uses_date_only(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "workspace")
    store.remember_turn("chat-1", "你好", "您好")

    memory = store.load("chat-1")
    assert memory.short_term
    assert all("T" not in turn.created_at for turn in memory.short_term)
    assert all(len(turn.created_at) == 10 for turn in memory.short_term)
