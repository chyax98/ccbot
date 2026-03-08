"""飞书通道适配器（带 Inbound Pipeline）。

集成 Dedup + Debounce + Queue 的完整入站处理流程。
消息解析、渲染、文件服务已拆分到 parser/renderer/file_service 模块。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from ccbot.channels.base import Channel
from ccbot.channels.feishu.file_service import upload_and_send_outputs, upload_file
from ccbot.channels.feishu.parser import extract_content
from ccbot.channels.feishu.renderer import (
    CONFIRM_RE,
    parse_confirm,
    send_confirm_card,
    send_file_message,
    send_single,
    split_content,
)
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

    def __init__(self, config: FeishuConfig, *, output_dir: Path | None = None) -> None:
        super().__init__()
        self.config = config
        self._output_dir = output_dir

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

        # 待确认 Future 池：{confirm_id -> asyncio.Future[str]}
        self._pending_confirms: dict[str, asyncio.Future[str]] = {}

        # 设置 Debounce 回调
        self._debounce.on_flush(self._on_debounced_messages)

    # ==================== Pipeline 入口 ====================

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

        messages = []
        for event_json in events:
            try:
                messages.append(json.loads(event_json))
            except Exception:
                continue

        if not messages:
            return

        first_event = messages[0]
        chat_id = first_event.get("message", {}).get("chat_id", "unknown")

        if len(messages) > 1:
            merged_content = self._merge_messages(messages)
            first_event["_merged"] = True
            first_event["_merged_count"] = len(messages)
            first_event["_merged_content"] = merged_content

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

    # ==================== 消息处理 ====================

    async def _process_event(self, event: dict) -> str:
        """实际处理飞书事件。"""
        message = event.get("message", {})
        sender = event.get("sender", {})

        message_id = message.get("message_id", "")
        chat_id = message.get("chat_id", "")
        chat_type = message.get("chat_type", "")
        sender_id = sender.get("sender_id", {}).get("open_id", "unknown")
        root_id = message.get("root_id")

        # 权限检查
        if not self._check_permissions(sender_id, chat_type):
            logger.debug("权限检查失败: sender_id={} chat_type={}", sender_id, chat_type)
            return ""

        # 提取内容（委托给 parser 模块，支持图片/文件下载）
        content = await extract_content(event, client=self._client)
        if not content:
            return ""

        reply_to = chat_id if chat_type == "group" else sender_id

        # 线程回复目标：有 root_id 时回复到线程根，避免多层嵌套
        reply_msg_id = root_id or message_id

        logger.info("处理消息: sender={} chat={} content={}", sender_id, chat_id, content[:60])

        query_start = time.time()

        # 添加表情表示正在处理
        reaction_id = await self._add_reaction(message_id, self.config.react_emoji)

        # 进度回调：静默期 + 间隔控制，发到 Thread
        last_progress_time = [query_start]
        total_tools = [0]
        silent_s = self.config.progress_silent_s
        interval_s = self.config.progress_interval_s

        async def progress_cb(msg: str) -> None:
            logger.info("[{}] 进度: {}", chat_id, msg)

            if msg.startswith("🔧"):
                total_tools[0] += 1
                now = time.time()
                elapsed = now - query_start
                since_last = now - last_progress_time[0]

                if elapsed >= silent_s and since_last >= interval_s:
                    last_progress_time[0] = now
                    with contextlib.suppress(Exception):
                        await self.send(
                            reply_to,
                            f"⏳ 已执行 **{total_tools[0]}** 次工具调用，仍在处理中...",
                            msg_type="progress",
                            reply_to_message_id=reply_msg_id,
                            reply_in_thread=True,
                        )

        def _tool_summary() -> str:
            if total_tools[0] <= 3:
                return ""
            return f"\n\n---\n🔧 共执行 **{total_tools[0]}** 次工具调用"

        async def result_sender(worker_name: str, result: str) -> None:
            """异步派发：Worker 结果发送到飞书 Thread。"""
            prefix = "✅" if not result.startswith("❌") else ""
            await self.send(
                reply_to,
                f"**{prefix} [{worker_name}]**\n\n{result}",
                reply_to_message_id=reply_msg_id,
                reply_in_thread=True,
            )

        try:
            reply = await self._handle_message(
                content, reply_to, sender_id, progress_cb, result_sender
            )

            # 检查 <<<CONFIRM>>> 交互标记
            confirm_match = CONFIRM_RE.search(reply)
            if confirm_match:
                pre_text = reply[: confirm_match.start()].strip()
                if pre_text:
                    await self.send(reply_to, pre_text, reply_to_message_id=reply_msg_id)

                question, options = parse_confirm(confirm_match.group(1))
                confirm_id = uuid.uuid4().hex[:8]
                cur_loop = asyncio.get_running_loop()
                confirm_future: asyncio.Future[str] = cur_loop.create_future()
                self._pending_confirms[confirm_id] = confirm_future
                await send_confirm_card(
                    self._client, reply_to, reply_msg_id, question, options, confirm_id
                )

                try:
                    choice = await asyncio.wait_for(
                        confirm_future, timeout=self.config.confirm_timeout_s
                    )
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    choice = "（超时，用户未响应）"
                finally:
                    self._pending_confirms.pop(confirm_id, None)

                total_tools[0] = 0
                follow_reply = await self._handle_message(
                    f"[用户选择: {choice}]", reply_to, sender_id, progress_cb
                )
                final = follow_reply + _tool_summary()
                await self.send(reply_to, final, reply_to_message_id=reply_msg_id)
                await self._do_upload_outputs(reply_to, reply_msg_id, query_start)
                return follow_reply
            else:
                final = reply + _tool_summary()
                await self.send(reply_to, final, reply_to_message_id=reply_msg_id)
                await self._do_upload_outputs(reply_to, reply_msg_id, query_start)
                return reply

        except Exception as e:
            logger.exception("处理消息失败: {}", e)
            error_msg = f"处理失败: {e}"
            await self.send(reply_to, error_msg, msg_type="error", reply_to_message_id=reply_msg_id)
            return error_msg

        finally:
            if reaction_id:
                await self._remove_reaction(message_id, reaction_id)

    def _check_permissions(self, sender_id: str, chat_type: str) -> bool:
        """检查权限。"""
        if chat_type == "group" and self.config.group_policy != "open":
            logger.debug("群聊策略拒绝: group_policy={}", self.config.group_policy)
            return False

        if chat_type == "p2p" and self.config.dm_policy == "pairing":
            return sender_id in self.config.allow_from

        allow_list = self.config.allow_from
        if not allow_list:
            logger.warning("allow_from 为空，拒绝所有访问")
            return False
        if "*" in allow_list:
            return True
        return sender_id in allow_list

    async def _do_upload_outputs(
        self, reply_to: str, reply_msg_id: str | None, since: float
    ) -> None:
        """委托给 file_service 上传 output 目录文件。"""
        await upload_and_send_outputs(
            self._client,
            self._output_dir,
            reply_to,
            reply_msg_id,
            since,
            send_file_message,
        )

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

        # 加载持久化的 dedup 数据
        await self._dedup.load(Path.home() / ".ccbot" / "dedup", "feishu")

        event_handler = (
            lark.EventDispatcherHandler.builder(
                self.config.encrypt_key or "",
                self.config.verification_token or "",
            )
            .register_p2_im_message_receive_v1(self._on_message_sync)
            .register_p2_card_action_trigger(self._on_card_action_sync)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self.config.app_id,
            self.config.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        ws_reconnect_delay = self.config.ws_reconnect_delay_s

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
                        time.sleep(ws_reconnect_delay)
            finally:
                ws_loop.close()

        self._running = True
        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("飞书通道已启动（Pipeline: Dedup → Debounce → Queue）")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止飞书通道。"""
        self._running = False

        await self._debounce.stop()
        await self._queue.stop()

        await self._dedup.persist(Path.home() / ".ccbot" / "dedup", "feishu")

        logger.info("飞书通道已停止")

    async def send(
        self,
        target: str,
        content: str,
        *,
        msg_type: str = "reply",
        reply_to_message_id: str | None = None,
        reply_in_thread: bool = False,
        **kwargs: Any,
    ) -> None:
        """发送消息，自动分段处理长内容。"""
        if not self._client:
            return

        for part in split_content(content, self.config.msg_split_max_len):
            await send_single(
                self._client,
                target,
                part,
                msg_type=msg_type,
                reply_to_message_id=reply_to_message_id,
                reply_in_thread=reply_in_thread,
            )

    # ==================== WebSocket 回调 ====================

    def _on_card_action_sync(self, data: Any) -> Any:
        """卡片按钮回调（WS 同步线程），解析 confirm_id 并唤醒等待的 Future。"""
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            CallBackToast,
            P2CardActionTriggerResponse,
        )

        response = P2CardActionTriggerResponse()
        try:
            event = getattr(data, "event", None)
            action = getattr(event, "action", None) if event else None
            value: dict = (getattr(action, "value", None) or {}) if action else {}

            confirm_id: str = value.get("confirm_id", "")
            choice: str = value.get("choice", "")

            if confirm_id and choice and self._loop:
                future = self._pending_confirms.get(confirm_id)
                if future and not future.done():
                    self._loop.call_soon_threadsafe(future.set_result, choice)
                    logger.info("卡片确认: confirm_id={} choice={}", confirm_id, choice)
                    toast = CallBackToast()
                    toast.type = "info"
                    toast.content = f"已选择: {choice}"
                    response.toast = toast
        except Exception as e:
            logger.error("卡片回调处理失败: {}", e)
        return response

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

            if sender.sender_type == "bot":
                return

            message_id = message.message_id

            # 群消息 require_mention 检查
            if self.config.require_mention and message.chat_type == "group" and self._bot_open_id:
                mentions = getattr(message, "mentions", None) or []
                bot_mentioned = any(
                    getattr(m.id, "open_id", None) == self._bot_open_id
                    for m in mentions
                    if m and getattr(m, "id", None)
                )
                if not bot_mentioned:
                    logger.debug("群消息未 @bot，跳过: {}", message_id)
                    return

            # 1. Dedup 检查
            if self._dedup.check(message_id):
                logger.debug("重复消息已跳过: {}", message_id)
                return

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
                    "sender_id": {
                        "open_id": sender.sender_id.open_id if sender.sender_id else "unknown"
                    },
                    "sender_type": sender.sender_type,
                },
            }
            event_json = json.dumps(event_dict, ensure_ascii=False)

            # 2. Debounce
            await self._debounce.enqueue(event_json)

        except Exception as e:
            logger.exception("Pipeline 处理失败: {}", e)

    # ==================== Emoji 反应 ====================

    async def _add_reaction(self, message_id: str, emoji_type: str) -> str | None:
        """添加表情反应，返回 reaction_id。"""
        if not self._client:
            return None

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
            response = await self._client.im.v1.message_reaction.acreate(request)
            if response.success() and response.data and response.data.reaction_id:
                return response.data.reaction_id
            logger.warning("添加表情失败: code={} msg={}", response.code, response.msg)
        except Exception as e:
            logger.warning("添加表情出错: {}", e)
        return None

    async def _remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """删除表情反应。"""
        if not self._client:
            return

        from lark_oapi.api.im.v1 import DeleteMessageReactionRequest

        try:
            request = (
                DeleteMessageReactionRequest.builder()
                .message_id(message_id)
                .reaction_id(reaction_id)
                .build()
            )
            await self._client.im.v1.message_reaction.adelete(request)
            logger.debug("已移除表情: message_id={} reaction_id={}", message_id, reaction_id)
        except Exception as e:
            logger.debug("移除表情失败: {}", e)

    # ==================== Bot 信息 ====================

    async def _fetch_bot_open_id(self) -> None:
        """获取 bot open_id。"""
        try:
            import httpx
            from lark_oapi.core.token.manager import TokenManager

            loop = asyncio.get_running_loop()
            token = await loop.run_in_executor(
                None, TokenManager.get_self_tenant_token, self._client._config
            )
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    "https://open.feishu.cn/open-apis/bot/v3/info",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
            data = resp.json()
            self._bot_open_id = (
                data.get("bot", {}).get("open_id", "") if data.get("code") == 0 else ""
            )
        except Exception as e:
            logger.warning("获取 bot open_id 失败: {}", e)
