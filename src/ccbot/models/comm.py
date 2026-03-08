"""多智能体通信数据模型。"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageType(StrEnum):
    """消息类型。"""

    DIRECT = "direct"  # 点对点
    BROADCAST = "broadcast"  # 广播
    REPORT = "report"  # 中途汇报 → Supervisor
    CLARIFY = "clarify"  # 请求澄清 → Supervisor


def _msg_id() -> str:
    return uuid4().hex[:12]


def _now() -> float:
    return time.time()


class CommMessage(BaseModel):
    """Worker 间 / Worker-Supervisor 通信消息。"""

    id: str = Field(default_factory=_msg_id)
    type: MessageType = MessageType.DIRECT
    source: str  # 发送者 (worker name / "supervisor")
    target: str = ""  # 空=广播, "supervisor"=上报, worker name=点对点
    session_id: str
    subject: str = ""
    body: str = ""
    timestamp: float = Field(default_factory=_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SharedEntry(BaseModel):
    """共享状态条目。"""

    key: str
    value: str
    author: str = ""  # 最后修改者
    updated_at: float = Field(default_factory=_now)
    version: int = 1
