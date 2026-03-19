"""File-backed supervisor memory store.

目标：
- 持久化 Claude runtime session_id，供 Supervisor 在重启后 resume
- 持久化短期记忆（最近若干轮对话）
- 提供长期记忆文件，供 Supervisor 通过文件维护长期知识
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_CHAT_ID_SANITIZER = re.compile(r"[^a-zA-Z0-9_.-]+")

# per-turn runtime_context 标签，存入短期记忆前过滤，避免污染历史快照
_RUNTIME_CTX_RE = re.compile(r"<runtime_context>[\s\S]*?</runtime_context>\s*", re.DOTALL)


def _strip_runtime_context(text: str) -> str:
    """移除 <runtime_context>...</runtime_context> 块，返回纯用户消息文本。"""
    return _RUNTIME_CTX_RE.sub("", text).strip()


def _memory_turn_date() -> str:
    """Return a stable UTC date string for short-term memory turns.

    只保留日期，不保留秒级时间戳，避免短期记忆文件和潜在 prompt 注入
    因时间抖动而降低 KV cache 命中。
    """
    return datetime.now(UTC).date().isoformat()


@dataclass(slots=True)
class MemoryTurn:
    role: str
    content: str
    created_at: str


@dataclass(slots=True)
class ConversationMemory:
    chat_id: str
    runtime_session_id: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    short_term: list[MemoryTurn] = field(default_factory=list)


class MemoryStore:
    """基于 workspace 文件系统的记忆存储。"""

    def __init__(self, workspace_path: Path, max_short_term_turns: int = 12) -> None:
        self._workspace_path = workspace_path
        self._root = workspace_path / "memory"
        self._max_short_term_turns = max_short_term_turns
        self._conversations_dir = (
            self._root / "conversations"
        )  # todo: claude agent sdk 是否会保留conversations
        self._conversations_dir.mkdir(parents=True, exist_ok=True)
        self._long_term_file = self._root / "long_term.md"
        self._bootstrap_files()

    @property
    def root(self) -> Path:
        return self._root

    @property
    def long_term_file(self) -> Path:
        return self._long_term_file

    def conversation_file(self, chat_id: str) -> Path:
        safe = _CHAT_ID_SANITIZER.sub("_", chat_id).strip("_") or "default"
        return self._conversations_dir / f"{safe}.json"

    def load(self, chat_id: str) -> ConversationMemory:
        path = self.conversation_file(chat_id)
        if not path.exists():
            return ConversationMemory(chat_id=chat_id)

        raw = json.loads(path.read_text(encoding="utf-8"))
        turns = [MemoryTurn(**turn) for turn in raw.get("short_term", [])]
        return ConversationMemory(
            chat_id=raw.get("chat_id", chat_id),
            runtime_session_id=raw.get("runtime_session_id", ""),
            updated_at=raw.get("updated_at", datetime.now(UTC).isoformat()),
            short_term=turns,
        )

    def save(self, memory: ConversationMemory) -> None:
        memory.updated_at = datetime.now(UTC).isoformat()
        path = self.conversation_file(memory.chat_id)
        payload = {
            "chat_id": memory.chat_id,
            "runtime_session_id": memory.runtime_session_id,
            "updated_at": memory.updated_at,
            "short_term": [asdict(turn) for turn in memory.short_term],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def remember_turn(
        self, chat_id: str, user_text: str, assistant_text: str
    ) -> ConversationMemory:
        # 存入前过滤 runtime_context 块：避免短期记忆被 Worker 状态、
        # 定时任务列表、时间戳等动态系统信息污染
        user_text = _strip_runtime_context(user_text)

        memory = self.load(chat_id)
        turns = deque(memory.short_term, maxlen=self._max_short_term_turns)
        now = _memory_turn_date()
        if user_text.strip():
            turns.append(MemoryTurn(role="user", content=user_text.strip(), created_at=now))
        if assistant_text.strip():
            turns.append(
                MemoryTurn(role="assistant", content=assistant_text.strip(), created_at=now)
            )
        memory.short_term = list(turns)
        self.save(memory)
        return memory

    def set_runtime_session(self, chat_id: str, session_id: str) -> ConversationMemory:
        memory = self.load(chat_id)
        memory.runtime_session_id = session_id
        self.save(memory)
        return memory

    def clear_conversation(self, chat_id: str) -> None:
        path = self.conversation_file(chat_id)
        if path.exists():
            path.unlink()

    def build_memory_prompt(self, chat_id: str) -> str:
        """冷启动时注入完整记忆：长期记忆 + 短期对话快照。"""
        memory = self.load(chat_id)
        sections: list[str] = []

        long_term = self._read_trimmed(self._long_term_file)
        if long_term:
            sections.append(
                "## 长期记忆（持久事实）\n"
                "以下内容来自 ccbot 自维护的长期记忆文件，仅保留稳定、可复用的信息：\n"  # todo: clear tag
                f"{long_term}"
            )

        if memory.short_term:
            rendered_turns = "\n".join(
                f"- {turn.role}: {turn.content}"
                for turn in memory.short_term[-self._max_short_term_turns :]
            )
            sections.append(
                "## 短期记忆（最近对话）\n"
                "以下内容来自最近若干轮对话持久化快照，可用于启动后的上下文恢复：\n"
                f"{rendered_turns}"
            )

        if not sections:
            return ""

        return (  # todo 改为 xml格式
            "\n\n---\n\n"
            "# ccbot Memory Context\n"
            "你正在读取 ccbot 自维护的记忆系统。\n"
            "- 长期记忆：稳定偏好、项目背景、持续约束\n"
            "- 短期记忆：最近对话摘要，用于启动后的续接\n"
            "若发现其中内容已过时，应在后续任务中更新对应文件而不是盲信。\n\n"
            + "\n\n".join(sections)
        )

    def build_long_term_prompt(self, chat_id: str) -> str:
        """Session resume 时只注入长期记忆。

        SDK resume 已恢复完整对话历史，短期记忆与 conversation history 重复且
        可能因格式差异产生语义冲突，故跳过。只注入长期记忆作为稳定事实参考。
        """
        long_term = self._read_trimmed(self._long_term_file)
        if not long_term:
            return ""

        return (
            "\n\n---\n\n"
            "# ccbot Memory Context\n"
            "当前会话已通过 session resume 恢复，对话历史完整保留。\n"
            "以下仅包含长期记忆（稳定事实），短期对话历史已在会话中，无需重复注入。\n"
            "若发现其中内容已过时，应在后续任务中更新对应文件而不是盲信。\n\n"
            "## 长期记忆（持久事实）\n"
            "以下内容来自 ccbot 自维护的长期记忆文件，仅保留稳定、可复用的信息：\n"
            f"{long_term}"
        )

    def _bootstrap_files(self) -> None:
        if not self._long_term_file.exists():
            self._long_term_file.write_text(
                "# 长期记忆\n\n"
                "记录稳定的用户偏好、项目约束、长期背景信息。\n\n"
                "维护原则：\n"
                "- 只保留长期有效的信息\n"
                "- 避免写入一次性任务细节\n"
                "- 过时信息要及时修正或删除\n",
                encoding="utf-8",
            )

    @staticmethod
    def _read_trimmed(path: Path, max_chars: int = 4000) -> str:
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8").strip()
        if len(content) <= max_chars:
            return content
        return content[:max_chars].rstrip() + "\n..."
