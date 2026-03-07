"""飞书通道适配器（带 Inbound Pipeline）。

集成 Dedup + Debounce + Queue 的完整入站处理流程。
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

from loguru import logger

from ccbot.channels.base import Channel
from ccbot.config import FeishuConfig
from ccbot.core import Debouncer, DedupCache, PerChatQueue

FEISHU_AVAILABLE = False
try:
    import lark_oapi as lark

    FEISHU_AVAILABLE = True
except ImportError:
    pass


class FeishuChannel(Channel):
    """飞书通道，集成 Inbound Pipeline。

    Pipeline 流程:
        1. Dedup: 基于 message_id 去重（内存+JSON 持久化）
        2. Debounce: 300ms 防抖合并，控制命令立即处理
        3. Queue: 每 chat 串行队列，异常隔离

    Args:
        config: 飞书配置
    """

    def __init__(self, config: FeishuConfig) -> None:
        super().__init__()
        self.config = config

        # Pipeline 组件
        self._dedup = DedupCache(ttl_ms=24 * 60 * 60 * 1000, max_size=1000)
        self._debounce = Debouncer[str](
            delay_ms=300,
            max_wait_ms=1000,
            key_extractor=self._extract_debounce_key,
            is_control_command=self._is_control_command,
        )
        self._queue = PerChatQueue()

        # Lark SDK 组件
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bot_open_id: str = ""

        # 设置 Debounce 回调
        self._debounce.on_flush(self._on_debounced_messages)

    @staticmethod
    def _extract_debounce_key(event_json: str) -> str:
        """从飞书事件提取防抖 key。"""
        try:
            event = json.loads(event_json)
            message = event.get("message", {})
            sender = event.get("sender", {})
            chat_id = message.get("chat_id", "unknown")
            sender_id = sender.get("sender_id", {}).get("open_id", "unknown")
            root_id = message.get("root_id")
            thread_key = f"thread:{root_id}" if root_id else "main"
            return f"feishu:{chat_id}:{thread_key}:{sender_id}"
        except Exception:
            return "unknown"

    @staticmethod
    def _is_control_command(event_json: str) -> bool:
        """检查是否为控制命令（不防抖）。"""
        control_commands = {"/new", "/stop", "/help", "/reset", "/clear"}
        try:
            event = json.loads(event_json)
            content = event.get("message", {}).get("content", "")
            text = json.loads(content).get("text", "").strip().lower()
            return text in control_commands
        except Exception:
            return False

    async def _on_debounced_messages(self, events: list[str]) -> None:
        """Debounce 完成后，将合并的消息加入队列处理。"""
        if not events:
            return

        # 解析合并消息
        messages = []
        for event_json in events:
            try:
                event = json.loads(event_json)
                messages.append(event)
            except Exception:
                continue

        if not messages:
            return

        # 提取合并后的内容
        first_event = messages[0]
        chat_id = first_event.get("message", {}).get("chat_id", "unknown")

        # 如果有多条消息，合并内容
        if len(messages) > 1:
            merged_content = self._merge_messages(messages)
            first_event["_merged"] = True
            first_event["_merged_count"] = len(messages)
            first_event["_merged_content"] = merged_content

        # 加入队列处理（使用闭包确保正确捕获事件）
        async def _handle() -> str:
            return await self._process_event(first_event)

        await self._queue.enqueue(chat_id, _handle)

    def _merge_messages(self, messages: list[dict]) -> str:
        """合并多条消息内容。"""
        parts = []
        for msg in messages:
            try:
                content = msg.get("message", {}).get("content", "{}")
                text = json.loads(content).get("text", "").strip()
                if text:
                    parts.append(text)
            except Exception:
                continue
        return "\n".join(parts)

    async def _process_event(self, event: dict) -> str:
        """实际处理飞书事件。"""
        message = event.get("message", {})
        sender = event.get("sender", {})

        message_id = message.get("message_id", "")
        chat_id = message.get("chat_id", "")
        chat_type = message.get("chat_type", "")
        sender_id = sender.get("sender_id", {}).get("open_id", "unknown")

        # 权限检查
        if not self._check_permissions(sender_id, chat_type):
            logger.debug("权限检查失败: sender_id={} chat_type={}", sender_id, chat_type)
            return ""

        # 提取内容
        content = self._extract_content(event)
        if not content:
            return ""

        reply_to = chat_id if chat_type == "group" else sender_id

        logger.info("处理消息: sender={} chat={} content={}", sender_id, chat_id, content[:60])

        # 添加 WINK 表情表示已收到消息
        await self._add_reaction(message_id, "WINK")

        # 进度回调：只打印日志，不发送消息给用户
        async def progress_cb(msg: str) -> None:
            logger.info("[{}] 进度: {}", chat_id, msg)

        try:
            # 调用业务处理器
            reply = await self._handle_message(content, reply_to, sender_id, progress_cb)
            await self.send(reply_to, reply)
            return reply
        except Exception as e:
            logger.exception("处理消息失败: {}", e)
            error_msg = f"处理失败: {e}"
            await self.send(reply_to, error_msg)
            return error_msg

    def _check_permissions(self, sender_id: str, chat_type: str) -> bool:
        """检查权限。"""
        allow_list = self.config.allow_from
        if not allow_list:
            logger.warning("allow_from 为空，拒绝所有访问")
            return False
        if "*" in allow_list:
            return True
        return sender_id in allow_list

    def _extract_content(self, event: dict) -> str:
        """提取消息内容。"""
        message = event.get("message", {})
        msg_type = message.get("message_type", "")
        content_str = message.get("content", "{}")

        try:
            content_json = json.loads(content_str)
        except json.JSONDecodeError:
            return ""

        # 使用合并后的内容（如果有）
        if event.get("_merged"):
            return event.get("_merged_content", "")

        if msg_type == "text":
            return content_json.get("text", "").strip()
        elif msg_type == "post":
            return self._extract_post_content(content_json)

        # 其他类型简化处理
        type_map = {
            "image": "[图片]",
            "audio": "[音频]",
            "file": "[文件]",
            "sticker": "[表情]",
        }
        return type_map.get(msg_type, f"[{msg_type}]")

    def _extract_post_content(self, content_json: dict) -> str:
        """提取富文本内容。"""
        texts = []
        post = content_json.get("post", content_json)

        for lang in ["zh_cn", "en_us", "ja_jp"]:
            if lang in post:
                content = post[lang]
                if isinstance(content, dict) and "content" in content:
                    for row in content["content"]:
                        for el in row:
                            if el.get("tag") in ("text", "a"):
                                texts.append(el.get("text", ""))
                            elif el.get("tag") == "at":
                                texts.append(f"@{el.get('user_name', 'user')}")
                break

        return " ".join(texts).strip()

    # ==================== Lark SDK 集成 ====================

    async def start(self) -> None:
        """启动飞书通道。"""
        if not FEISHU_AVAILABLE:
            logger.error("飞书 SDK 未安装: uv pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("飞书 app_id 和 app_secret 未配置")
            return

        self._loop = asyncio.get_running_loop()

        self._client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        await self._fetch_bot_open_id()

        event_handler = (
            lark.EventDispatcherHandler.builder(
                self.config.encrypt_key or "",
                self.config.verification_token or "",
            )
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        def run_ws() -> None:
            import time

            import lark_oapi.ws.client as _lark_ws_client

            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _lark_ws_client.loop = ws_loop
            attempt = 0
            try:
                while self._running:
                    try:
                        if attempt > 0:
                            logger.info("飞书 WebSocket 重连 (第 {} 次)...", attempt)
                        self._ws_client.start()
                    except Exception as e:
                        logger.warning("飞书 WebSocket 断开: {}", e)
                    if self._running:
                        attempt += 1
                        time.sleep(2)
            finally:
                ws_loop.close()

        self._running = True
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("飞书通道已启动（集成 Pipeline: Dedup → Debounce → Queue）")

        # 保持运行
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止飞书通道。"""
        self._running = False

        # 停止 Pipeline
        await self._debounce.stop()
        await self._queue.stop()

        # 持久化去重缓存
        await self._dedup.persist(Path.home() / ".ccbot" / "dedup", "feishu")

        logger.info("飞书通道已停止")

    async def send(self, target: str, content: str, **kwargs: Any) -> None:
        """发送消息。"""
        if not self._client:
            return

        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        receive_id_type = "chat_id" if target.startswith("oc_") else "open_id"
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": content}],
        }

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(target)
                    .msg_type("interactive")
                    .content(json.dumps(card, ensure_ascii=False))
                    .build()
                )
                .build()
            )
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, self._client.im.v1.message.create, request)
            if not response.success():
                logger.error("发送失败: code={}, msg={}", response.code, response.msg)
        except Exception as e:
            logger.error("发送消息出错: {}", e)

    def _on_message_sync(self, data: Any) -> None:
        """WebSocket 回调（同步线程）。"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message_async(data), self._loop)

    async def _on_message_async(self, data: Any) -> None:
        """异步处理入站消息，进入 Pipeline。"""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            # 跳过 bot 消息
            if sender.sender_type == "bot":
                return

            message_id = message.message_id

            # 1. Dedup 检查
            if self._dedup.check(message_id):
                logger.debug("重复消息已跳过: {}", message_id)
                return

            # 构造事件 JSON
            event_dict = {
                "message": {
                    "message_id": message_id,
                    "chat_id": message.chat_id,
                    "chat_type": message.chat_type,
                    "message_type": message.message_type,
                    "content": message.content,
                    "root_id": getattr(message, "root_id", None),
                },
                "sender": {
                    "sender_id": {"open_id": sender.sender_id.open_id if sender.sender_id else "unknown"},
                    "sender_type": sender.sender_type,
                },
            }
            event_json = json.dumps(event_dict, ensure_ascii=False)

            # 2. Debounce（控制命令会立即 flush）
            await self._debounce.enqueue(event_json)

        except Exception as e:
            logger.exception("Pipeline 处理失败: {}", e)

    async def _add_reaction(self, message_id: str, emoji_type: str) -> None:
        """添加表情反应。"""
        if not self._client:
            return

        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        try:
            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._client.im.v1.message_reaction.create, request)
        except Exception as e:
            logger.debug("添加表情失败: {}", e)

    async def _fetch_bot_open_id(self) -> None:
        """获取 bot open_id。"""
        try:
            import requests as _requests
            from lark_oapi.core.token.manager import TokenManager

            def _get() -> str:
                token = TokenManager.get_self_tenant_token(self._client._config)
                resp = _requests.get(
                    "https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                data = resp.json()
                return data.get("bot", {}).get("open_id", "") if data.get("code") == 0 else ""

            loop = asyncio.get_running_loop()
            self._bot_open_id = await loop.run_in_executor(None, _get)
        except Exception as e:
            logger.warning("获取 bot open_id 失败: {}", e)

    async def _send_typing_indicator(self, chat_id: str) -> None:
        """发送"正在输入"状态（Typing Indicator）。

        飞书 API: POST /open-apis/im/v1/chats/{chat_id}/typing
        """
        try:
            import requests
            from lark_oapi.core.token.manager import TokenManager

            def _send() -> None:
                token = TokenManager.get_self_tenant_token(self._client._config)
                resp = requests.post(
                    f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/typing",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"type": "text"},
                    timeout=5,
                )
                if resp.status_code != 200:
                    logger.debug("发送 typing 状态失败: {}", resp.text)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _send)
        except Exception as e:
            # Typing 状态是可选功能，失败不影响主流程
            logger.debug("发送 typing 状态出错: {}", e)

