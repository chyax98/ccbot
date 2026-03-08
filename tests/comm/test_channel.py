"""WorkerChannel 单元测试。"""

from __future__ import annotations

from ccbot.comm.bus import InMemoryBus
from ccbot.comm.channel import WorkerChannel
from ccbot.comm.context import InMemoryContext


def test_system_prompt_addition():
    """system_prompt_addition 包含 Worker 名称和同伴信息。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()

    channel = WorkerChannel(
        bus=bus,
        context=ctx,
        session_id="s1",
        worker_name="alice",
        peer_names=["bob", "charlie"],
    )

    prompt = channel.system_prompt_addition
    assert "alice" in prompt
    assert "bob" in prompt
    assert "charlie" in prompt
    assert "ccbot_send_message" in prompt
    assert "ccbot_read_messages" in prompt
    assert "ccbot_set_shared" in prompt
    assert "ccbot_get_shared" in prompt
    assert "ccbot_report_progress" in prompt
    # SDK 方案不再需要 worker_name 参数提示
    assert "worker_name" not in prompt


def test_mcp_servers_config():
    """mcp_servers 生成 sdk 类型配置。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()

    channel = WorkerChannel(
        bus=bus,
        context=ctx,
        session_id="s1",
        worker_name="alice",
        peer_names=["bob"],
    )

    config = channel.mcp_servers
    assert "ccbot-comm" in config
    assert config["ccbot-comm"]["type"] == "sdk"
    assert config["ccbot-comm"]["name"] == "ccbot-comm"
    assert "instance" in config["ccbot-comm"]


def test_single_peer():
    """只有一个同伴时 prompt 正确。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()

    channel = WorkerChannel(
        bus=bus,
        context=ctx,
        session_id="s1",
        worker_name="alice",
        peer_names=["bob"],
    )

    prompt = channel.system_prompt_addition
    assert "bob" in prompt
    assert "alice" in prompt


def test_no_peers():
    """无同伴时 prompt 不报错。"""
    bus = InMemoryBus()
    ctx = InMemoryContext()

    channel = WorkerChannel(
        bus=bus,
        context=ctx,
        session_id="s1",
        worker_name="alice",
        peer_names=[],
    )

    prompt = channel.system_prompt_addition
    assert "alice" in prompt
