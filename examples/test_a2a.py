#!/usr/bin/env python3
"""A2A 协议测试脚本：测试 ccbot A2A 服务器的各个端点。"""

import asyncio
import json

import httpx


async def test_agent_card(base_url: str) -> None:
    """测试 Agent Card 端点。"""
    print("\\n=== 测试 Agent Card ===")
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{base_url}/.well-known/agent.json")
        print(f"状态码: {resp.status_code}")
        print(f"响应: {json.dumps(resp.json(), indent=2, ensure_ascii=False)}")


async def test_message_send(base_url: str, message: str) -> None:
    """测试同步消息发送。"""
    print("\\n=== 测试 message/send ===")
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "contextId": "test-context-1",
            "message": message,
        },
        "id": 1,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{base_url}/rpc", json=payload)
        print(f"状态码: {resp.status_code}")
        result = resp.json()
        print(f"响应: {json.dumps(result, indent=2, ensure_ascii=False)}")


async def test_message_stream(base_url: str, message: str) -> None:
    """测试流式消息发送（SSE）。"""
    print("\\n=== 测试 message/stream ===")
    payload = {
        "jsonrpc": "2.0",
        "method": "message/stream",
        "params": {
            "contextId": "test-context-2",
            "message": message,
        },
        "id": 2,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        async with client.stream("POST", f"{base_url}/rpc", json=payload) as resp:
            print(f"状态码: {resp.status_code}")
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    print(f"事件数据: {data}")
                elif line.startswith("event:"):
                    event = line[6:].strip()
                    print(f"事件类型: {event}")


async def main() -> None:
    base_url = "http://localhost:8765"

    print("🐈 ccbot A2A 协议测试")
    print(f"服务器地址: {base_url}")

    # 1. 测试 Agent Card
    await test_agent_card(base_url)

    # 2. 测试同步消息
    await test_message_send(base_url, "你好，请介绍一下你自己")

    # 3. 测试流式消息
    await test_message_stream(base_url, "用 Python 写一个快速排序算法")


if __name__ == "__main__":
    asyncio.run(main())
