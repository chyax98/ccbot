"""MessageBus 单元测试。"""

from __future__ import annotations

import asyncio

import pytest

from ccbot.comm.bus import InMemoryBus
from ccbot.models.comm import CommMessage, MessageType


@pytest.fixture
async def bus():
    b = InMemoryBus()
    await b.create_session("s1", ["alice", "bob", "charlie"])
    yield b
    await b.close_session("s1")


async def test_direct_message(bus: InMemoryBus):
    """点对点消息只送到目标 Worker。"""
    msg = CommMessage(
        type=MessageType.DIRECT,
        source="alice",
        target="bob",
        session_id="s1",
        subject="hello",
        body="hi bob",
    )
    await bus.send(msg)

    bob_msgs = await bus.receive("s1", "bob")
    assert len(bob_msgs) == 1
    assert bob_msgs[0].subject == "hello"

    alice_msgs = await bus.receive("s1", "alice")
    assert len(alice_msgs) == 0

    charlie_msgs = await bus.receive("s1", "charlie")
    assert len(charlie_msgs) == 0


async def test_broadcast_message(bus: InMemoryBus):
    """广播消息送到所有 Worker（除发送者）。"""
    msg = CommMessage(
        type=MessageType.BROADCAST,
        source="alice",
        target="",
        session_id="s1",
        subject="announcement",
        body="hello everyone",
    )
    await bus.send(msg)

    bob_msgs = await bus.receive("s1", "bob")
    assert len(bob_msgs) == 1

    charlie_msgs = await bus.receive("s1", "charlie")
    assert len(charlie_msgs) == 1

    # 发送者不收到自己的广播
    alice_msgs = await bus.receive("s1", "alice")
    assert len(alice_msgs) == 0


async def test_report_triggers_callback(bus: InMemoryBus):
    """上报消息触发 on_report 回调。"""
    reports: list[tuple[str, CommMessage]] = []

    async def on_report(name: str, msg: CommMessage) -> None:
        reports.append((name, msg))

    bus.on_report(on_report)

    msg = CommMessage(
        type=MessageType.REPORT,
        source="alice",
        target="supervisor",
        session_id="s1",
        subject="50%",
        body="halfway done",
    )
    await bus.send(msg)

    assert len(reports) == 1
    assert reports[0][0] == "alice"
    assert reports[0][1].subject == "50%"


async def test_clarify_triggers_callback(bus: InMemoryBus):
    """澄清请求也触发 on_report 回调。"""
    reports: list[tuple[str, CommMessage]] = []

    async def on_report(name: str, msg: CommMessage) -> None:
        reports.append((name, msg))

    bus.on_report(on_report)

    msg = CommMessage(
        type=MessageType.CLARIFY,
        source="bob",
        target="supervisor",
        session_id="s1",
        subject="question",
        body="need clarification",
    )
    await bus.send(msg)

    assert len(reports) == 1
    assert reports[0][0] == "bob"


async def test_receive_since_filter(bus: InMemoryBus):
    """since 参数过滤旧消息。"""
    import time

    t_before = time.time()
    await asyncio.sleep(0.01)

    msg1 = CommMessage(
        type=MessageType.DIRECT,
        source="alice",
        target="bob",
        session_id="s1",
        subject="old",
        body="old message",
        timestamp=t_before - 1,
    )
    msg2 = CommMessage(
        type=MessageType.DIRECT,
        source="alice",
        target="bob",
        session_id="s1",
        subject="new",
        body="new message",
    )
    await bus.send(msg1)
    await bus.send(msg2)

    msgs = await bus.receive("s1", "bob", since=t_before)
    assert len(msgs) == 1
    assert msgs[0].subject == "new"


async def test_history(bus: InMemoryBus):
    """get_history 返回所有消息记录。"""
    for i in range(3):
        msg = CommMessage(
            type=MessageType.BROADCAST,
            source="alice",
            target="",
            session_id="s1",
            subject=f"msg-{i}",
            body=f"body-{i}",
        )
        await bus.send(msg)

    history = await bus.get_history("s1")
    assert len(history) == 3


async def test_session_isolation():
    """不同 session 的消息互不影响。"""
    bus = InMemoryBus()
    await bus.create_session("s1", ["alice", "bob"])
    await bus.create_session("s2", ["alice", "bob"])

    msg = CommMessage(
        type=MessageType.DIRECT,
        source="alice",
        target="bob",
        session_id="s1",
        subject="s1 only",
        body="",
    )
    await bus.send(msg)

    s1_msgs = await bus.receive("s1", "bob")
    assert len(s1_msgs) == 1

    s2_msgs = await bus.receive("s2", "bob")
    assert len(s2_msgs) == 0

    await bus.close_session("s1")
    await bus.close_session("s2")


async def test_get_worker_names(bus: InMemoryBus):
    """get_worker_names 返回正确的 Worker 列表。"""
    names = bus.get_worker_names("s1")
    assert set(names) == {"alice", "bob", "charlie"}


async def test_send_to_nonexistent_session(bus: InMemoryBus):
    """向不存在的 session 发消息不报错。"""
    msg = CommMessage(
        type=MessageType.DIRECT,
        source="alice",
        target="bob",
        session_id="nonexistent",
        subject="test",
        body="",
    )
    await bus.send(msg)  # 不应报错


async def test_receive_clears_inbox(bus: InMemoryBus):
    """receive 后消息从 inbox 中清除。"""
    msg = CommMessage(
        type=MessageType.DIRECT,
        source="alice",
        target="bob",
        session_id="s1",
        subject="test",
        body="",
    )
    await bus.send(msg)

    msgs = await bus.receive("s1", "bob")
    assert len(msgs) == 1

    # 再次读取应为空
    msgs = await bus.receive("s1", "bob")
    assert len(msgs) == 0
