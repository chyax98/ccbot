"""SharedContext 单元测试。"""

from __future__ import annotations

import json

import pytest

from ccbot.comm.context import InMemoryContext


@pytest.fixture
async def ctx():
    c = InMemoryContext()
    await c.create_session("s1")
    yield c
    await c.close_session("s1")


async def test_set_and_get(ctx: InMemoryContext):
    """基本的 set/get。"""
    await ctx.set("s1", "key1", "value1", author="alice")
    result = await ctx.get("s1", "key1")
    assert result == "value1"


async def test_get_nonexistent(ctx: InMemoryContext):
    """获取不存在的 key 返回 None。"""
    result = await ctx.get("s1", "nonexistent")
    assert result is None


async def test_overwrite(ctx: InMemoryContext):
    """覆盖写入。"""
    await ctx.set("s1", "key1", "v1", author="alice")
    await ctx.set("s1", "key1", "v2", author="bob")
    result = await ctx.get("s1", "key1")
    assert result == "v2"


async def test_version_increment(ctx: InMemoryContext):
    """版本号递增。"""
    await ctx.set("s1", "key1", "v1")
    await ctx.set("s1", "key1", "v2")
    await ctx.set("s1", "key1", "v3")

    snapshot = await ctx.snapshot("s1")
    data = json.loads(snapshot)
    assert data["key1"]["version"] == 3


async def test_list_keys(ctx: InMemoryContext):
    """list_keys 返回所有键名。"""
    await ctx.set("s1", "a", "1")
    await ctx.set("s1", "b", "2")
    await ctx.set("s1", "c", "3")

    keys = await ctx.list_keys("s1")
    assert set(keys) == {"a", "b", "c"}


async def test_list_keys_empty(ctx: InMemoryContext):
    """空 session 的 list_keys。"""
    keys = await ctx.list_keys("s1")
    assert keys == []


async def test_snapshot(ctx: InMemoryContext):
    """snapshot 生成完整的 JSON 快照。"""
    await ctx.set("s1", "key1", "v1", author="alice")
    await ctx.set("s1", "key2", "v2", author="bob")

    snapshot = await ctx.snapshot("s1")
    data = json.loads(snapshot)

    assert "key1" in data
    assert data["key1"]["value"] == "v1"
    assert data["key1"]["author"] == "alice"
    assert data["key2"]["value"] == "v2"
    assert data["key2"]["author"] == "bob"


async def test_snapshot_empty(ctx: InMemoryContext):
    """空 session 的 snapshot 返回空字符串。"""
    snapshot = await ctx.snapshot("s1")
    assert snapshot == ""


async def test_session_isolation():
    """不同 session 的数据隔离。"""
    ctx = InMemoryContext()
    await ctx.create_session("s1")
    await ctx.create_session("s2")

    await ctx.set("s1", "key", "s1_value")
    await ctx.set("s2", "key", "s2_value")

    assert await ctx.get("s1", "key") == "s1_value"
    assert await ctx.get("s2", "key") == "s2_value"

    await ctx.close_session("s1")
    await ctx.close_session("s2")


async def test_closed_session(ctx: InMemoryContext):
    """关闭 session 后操作不报错。"""
    await ctx.close_session("s1")

    await ctx.set("s1", "key", "value")  # 不报错
    result = await ctx.get("s1", "key")
    assert result is None

    keys = await ctx.list_keys("s1")
    assert keys == []
