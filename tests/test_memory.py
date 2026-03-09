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
