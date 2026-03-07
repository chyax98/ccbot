"""A2A 协议 HTTP 服务器（Agent-to-Agent 通信）。

基于 Google A2A 协议规范：
- Agent Card: /.well-known/agent.json
- JSON-RPC 2.0: /rpc 端点
- 方法: message/send (同步), message/stream (SSE 流式)
- contextId 映射到 nanobot 的 chat_id，支持多轮对话
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from ccbot.config import A2AConfig
from ccbot.team import AgentTeam


class A2AServer:
    """A2A 协议 HTTP 服务器。

    将 nanobot 的 AgentTeam 暴露为 A2A 兼容的 HTTP 端点，
    使得多个 nanobot 实例可以跨机器通信。

    核心映射：
    - A2A contextId → nanobot chat_id（多轮对话）
    - A2A message/send → team.ask()（同步）
    - A2A message/stream → team.ask() + SSE（流式）
    """

    def __init__(self, team: AgentTeam, config: A2AConfig) -> None:
        self.team = team
        self.config = config
        self.app = FastAPI(title="nanobot A2A Server")
        self._setup_routes()

    def _setup_routes(self) -> None:
        @self.app.get("/.well-known/agent.json")
        async def agent_card() -> dict[str, Any]:
            """Agent Card 端点：描述 agent 能力和端点。"""
            base_url = f"http://{self.config.host}:{self.config.port}"
            return {
                "name": self.config.name,
                "description": self.config.description,
                "version": "1.0.0",
                "capabilities": [
                    "message/send",
                    "message/stream",
                ],
                "endpoints": {
                    "message/send": f"{base_url}/rpc",
                    "message/stream": f"{base_url}/rpc",
                },
            }

        @self.app.post("/rpc", response_model=None)
        async def rpc_handler(request: Request) -> JSONResponse | EventSourceResponse:
            """JSON-RPC 2.0 端点：处理 message/send 和 message/stream。"""
            try:
                body = await request.json()
            except Exception as e:
                logger.warning("A2A RPC 请求解析失败: {}", e)
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None,
                    },
                    status_code=400,
                )

            method = body.get("method")
            params = body.get("params", {})
            rpc_id = body.get("id")

            logger.info("A2A RPC: method={} params={}", method, params)

            if method == "message/send":
                return await self._handle_message_send(params, rpc_id)
            elif method == "message/stream":
                return await self._handle_message_stream(params, rpc_id)
            else:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "error": {"code": -32601, "message": "Method not found"},
                        "id": rpc_id,
                    },
                    status_code=404,
                )

    async def _handle_message_send(self, params: dict[str, Any], rpc_id: Any) -> JSONResponse:
        """处理同步消息请求。"""
        context_id = params.get("contextId", "a2a-default")
        message = params.get("message", "")

        if not message:
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Invalid params: message required"},
                    "id": rpc_id,
                }
            )

        try:
            reply = await self.team.ask(context_id, message)
            logger.info("A2A message/send 完成: contextId={} reply_len={}", context_id, len(reply))

            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "result": {
                        "contextId": context_id,
                        "message": reply,
                    },
                    "id": rpc_id,
                }
            )
        except Exception as e:
            logger.error("A2A message/send 失败: {}", e)
            return JSONResponse(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32000, "message": f"Internal error: {e}"},
                    "id": rpc_id,
                }
            )

    async def _handle_message_stream(
        self, params: dict[str, Any], rpc_id: Any
    ) -> EventSourceResponse:
        """处理流式消息请求（SSE）。"""
        context_id = params.get("contextId", "a2a-default")
        message = params.get("message", "")

        async def event_generator():
            if not message:
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "error": {
                                "code": -32602,
                                "message": "Invalid params: message required",
                            },
                            "id": rpc_id,
                        }
                    ),
                }
                return

            try:
                # 使用队列收集进度消息
                progress_queue: asyncio.Queue[str | None] = asyncio.Queue()

                async def on_progress(msg: str) -> None:
                    await progress_queue.put(msg)

                # 在后台任务中执行 ask
                async def run_task():
                    try:
                        reply = await self.team.ask(context_id, message, on_progress=on_progress)
                        await progress_queue.put(None)  # 标记完成
                        return reply
                    except Exception as e:
                        await progress_queue.put(None)
                        raise e

                task = asyncio.create_task(run_task())

                # 流式发送进度事件
                while True:
                    msg = await progress_queue.get()
                    if msg is None:
                        break
                    yield {
                        "event": "progress",
                        "data": json.dumps({"message": msg}),
                    }

                # 等待任务完成并获取结果
                reply = await task

                logger.info(
                    "A2A message/stream 完成: contextId={} reply_len={}", context_id, len(reply)
                )

                # 发送最终结果
                yield {
                    "event": "result",
                    "data": json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "result": {
                                "contextId": context_id,
                                "message": reply,
                            },
                            "id": rpc_id,
                        }
                    ),
                }

            except Exception as e:
                logger.error("A2A message/stream 失败: {}", e)
                yield {
                    "event": "error",
                    "data": json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "error": {"code": -32000, "message": f"Internal error: {e}"},
                            "id": rpc_id,
                        }
                    ),
                }

        return EventSourceResponse(event_generator())
