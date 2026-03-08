"""飞书通道适配器（带 Inbound Pipeline）。

集成 Dedup + Debounce + Queue 的完整入站处理流程。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import threading
import time
import uuid
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


# 图片扩展名 → 走 im.v1.image 上传
_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# 文件扩展名 → Feishu file_type 值（其余用 "stream"）
_EXT_TO_FILE_TYPE: dict[str, str] = {
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
    ".mp4": "mp4",
    ".opus": "opus",
    ".m4a": "opus",
}


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

        # 提取内容（异步，支持图片/文件下载）
        content = await self._extract_content(event)
        if not content:
            return ""

        reply_to = chat_id if chat_type == "group" else sender_id

        # 线程回复目标：有 root_id 时回复到线程根，避免多层嵌套；否则回复原始消息
        reply_msg_id = root_id or message_id

        logger.info("处理消息: sender={} chat={} content={}", sender_id, chat_id, content[:60])

        query_start = time.time()  # 用于过滤 output/ 里的旧文件

        # 添加表情表示正在处理
        reaction_id = await self._add_reaction(message_id, self.config.react_emoji)

        # 进度回调：前 30s 静默（emoji 反应足够），之后每 60s 最多一条，发到 Thread
        last_progress_time = [query_start]
        total_tools = [0]

        async def progress_cb(msg: str) -> None:
            logger.info("[{}] 进度: {}", chat_id, msg)

            if msg.startswith("🔧"):
                total_tools[0] += 1
                now = time.time()
                elapsed = now - query_start
                since_last = now - last_progress_time[0]

                # 静默期 30s + 之后每 60s 一条
                if elapsed >= 30 and since_last >= 60:
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
            """生成工具调用计数摘要，追加到最终回复。"""
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
            confirm_match = _CONFIRM_RE.search(reply)
            if confirm_match:
                # 发送 CONFIRM 前的文本（如果有）
                pre_text = reply[: confirm_match.start()].strip()
                if pre_text:
                    await self.send(reply_to, pre_text, reply_to_message_id=reply_msg_id)

                # 解析 question & options，发送按钮卡片
                question, options = _parse_confirm(confirm_match.group(1))
                confirm_id = uuid.uuid4().hex[:8]
                cur_loop = asyncio.get_running_loop()
                confirm_future: asyncio.Future[str] = cur_loop.create_future()
                self._pending_confirms[confirm_id] = confirm_future
                await self._send_confirm_card(reply_to, reply_msg_id, question, options, confirm_id)

                # 等待用户点击按钮（最多 5 分钟）
                try:
                    choice = await asyncio.wait_for(confirm_future, timeout=300)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    choice = "（超时，用户未响应）"
                finally:
                    self._pending_confirms.pop(confirm_id, None)

                # 将用户选择发回 Claude，获取后续回复
                total_tools[0] = 0
                follow_reply = await self._handle_message(
                    f"[用户选择: {choice}]", reply_to, sender_id, progress_cb
                )
                final = follow_reply + _tool_summary()
                await self.send(reply_to, final, reply_to_message_id=reply_msg_id)
                await self._upload_and_send_outputs(reply_to, reply_msg_id, since=query_start)
                return follow_reply
            else:
                final = reply + _tool_summary()
                await self.send(reply_to, final, reply_to_message_id=reply_msg_id)
                await self._upload_and_send_outputs(reply_to, reply_msg_id, since=query_start)
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

    async def _extract_content(self, event: dict) -> str:
        """提取消息内容（异步，支持图片/文件下载）。"""
        message = event.get("message", {})
        msg_type = message.get("message_type", "")
        content_str = message.get("content", "{}")
        message_id = message.get("message_id", "")

        try:
            content_json = json.loads(content_str)
        except json.JSONDecodeError:
            return ""

        # 使用合并后的内容（如果有）
        if event.get("_merged"):
            return str(event.get("_merged_content", ""))

        if msg_type == "text":
            return str(content_json.get("text", "")).strip()

        elif msg_type == "post":
            return self._extract_post_content(content_json)

        elif msg_type == "image":
            image_key = content_json.get("image_key", "")
            if image_key and message_id:
                path = await self._download_resource(message_id, image_key, "image")
                if path:
                    return f"[图片已下载，路径: {path}，请用 Read 工具查看]"
            return "[图片（下载失败）]"

        elif msg_type == "file":
            file_key = content_json.get("file_key", "")
            file_name = content_json.get("file_name", "file")
            if file_key and message_id:
                path = await self._download_resource(message_id, file_key, "file", file_name)
                if path:
                    return f"[文件 '{file_name}' 已下载，路径: {path}，可用 Read/Bash 工具处理]"
            return f"[文件 '{file_name}'（下载失败）]"

        type_map = {
            "audio": "[音频]",
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

    async def _download_resource(
        self,
        message_id: str,
        file_key: str,
        resource_type: str,
        file_name: str = "",
    ) -> str | None:
        """下载飞书消息资源（图片/文件），返回本地文件路径。"""
        if not self._client:
            return None

        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
            # 使用异步版本，无需 run_in_executor
            response = await self._client.im.v1.message_resource.aget(request)

            if not response.success() or not response.file:
                logger.warning("下载资源失败: code={} msg={}", response.code, response.msg)
                return None

            # 确保 tmp 目录存在
            tmp_dir = Path.home() / ".ccbot" / "tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)

            # 清理 24h 前的旧文件
            now = time.time()
            for f in tmp_dir.iterdir():
                if f.is_file() and now - f.stat().st_mtime > 86400:
                    f.unlink(missing_ok=True)

            fname = file_name or response.file_name or f"{resource_type}_{int(now)}"
            dest = tmp_dir / f"{int(now * 1000)}_{fname}"
            dest.write_bytes(response.file.read())

            logger.info("资源下载完成: {} → {}", file_key, dest)
            return str(dest)

        except Exception as e:
            logger.warning("下载资源出错: {}", e)
            return None

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
            .register_p2_card_action_trigger(self._on_card_action_sync)
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
        """发送消息，自动分段处理长内容。

        Args:
            target: 目标 ID（chat_id 或 open_id），reply 模式下仅作为 fallback
            content: 消息内容（Markdown 格式）
            msg_type: 消息类型 - "reply"(默认), "progress"(青色header), "error"(红色header)
            reply_to_message_id: 原始消息 ID，设置后回复该消息
            reply_in_thread: True 时以话题形式回复（进度消息），False 时直接回复（默认）
        """
        if not self._client:
            return

        for part in _split_content(content):
            await self._send_single(
                target, part, msg_type=msg_type,
                reply_to_message_id=reply_to_message_id,
                reply_in_thread=reply_in_thread,
            )

    async def _send_single(
        self,
        target: str,
        content: str,
        *,
        msg_type: str = "reply",
        reply_to_message_id: str | None = None,
        reply_in_thread: bool = False,
    ) -> None:
        """发送单条消息（不分段）。

        渲染策略：
        - progress/error: 卡片(Schema 2.0) + 彩色 header
        - 含代码块或表格: 卡片(Schema 2.0)
        - 普通文本: post + md 标签
        """
        header_map = {
            "progress": ("⏳ 执行中", "turquoise"),
            "error": ("❌ 出错", "red"),
        }
        use_card = msg_type in header_map or _should_use_card(content)

        if use_card:
            card: dict[str, Any] = {
                "schema": "2.0",
                "config": {"wide_screen_mode": True},
                "body": {
                    "elements": [{"tag": "markdown", "content": content}],
                },
            }
            if msg_type in header_map:
                title, color = header_map[msg_type]
                card["header"] = {
                    "title": {"tag": "plain_text", "content": title},
                    "template": color,
                }
            feishu_msg_type = "interactive"
            feishu_content = json.dumps(card, ensure_ascii=False)
        else:
            post = {"zh_cn": {"content": [[{"tag": "md", "text": content}]]}}
            feishu_msg_type = "post"
            feishu_content = json.dumps(post, ensure_ascii=False)

        try:
            if reply_to_message_id:
                from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

                request = (
                    ReplyMessageRequest.builder()
                    .message_id(reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(feishu_content)
                        .msg_type(feishu_msg_type)
                        .reply_in_thread(reply_in_thread)
                        .build()
                    )
                    .build()
                )
                response = await self._client.im.v1.message.areply(request)
            else:
                from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

                receive_id_type = "chat_id" if target.startswith("oc_") else "open_id"
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(target)
                        .msg_type(feishu_msg_type)
                        .content(feishu_content)
                        .build()
                    )
                    .build()
                )
                response = await self._client.im.v1.message.acreate(request)

            if not response.success():
                logger.error("发送失败: code={}, msg={}", response.code, response.msg)

        except Exception as e:
            logger.error("发送消息出错: {}", e)

    async def _send_confirm_card(
        self,
        target: str,
        reply_to_message_id: str | None,
        question: str,
        options: list[str],
        confirm_id: str,
    ) -> None:
        """发送带按钮的 Schema 2.0 交互确认卡片。

        按钮 value 格式: {"confirm_id": "<id>", "choice": "<option>"}
        """
        buttons = []
        for i, opt in enumerate(options[:4]):  # 飞书行动区最多 4 个按钮
            buttons.append(
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": opt},
                    "type": "primary" if i == 0 else "default",
                    "value": {"confirm_id": confirm_id, "choice": opt},
                }
            )

        card: dict = {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "❓ 等待确认"},
                "template": "blue",
            },
            "body": {
                "elements": [
                    {"tag": "markdown", "content": question},
                    *buttons,  # Schema 2.0 不支持 action 容器，按钮直接放 elements
                ],
            },
        }
        feishu_content = json.dumps(card, ensure_ascii=False)

        try:
            if reply_to_message_id:
                from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

                request = (
                    ReplyMessageRequest.builder()
                    .message_id(reply_to_message_id)
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .content(feishu_content)
                        .msg_type("interactive")
                        .reply_in_thread(False)
                        .build()
                    )
                    .build()
                )
                response = await self._client.im.v1.message.areply(request)
            else:
                from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

                receive_id_type = "chat_id" if target.startswith("oc_") else "open_id"
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(target)
                        .msg_type("interactive")
                        .content(feishu_content)
                        .build()
                    )
                    .build()
                )
                response = await self._client.im.v1.message.acreate(request)
            if not response.success():
                logger.error("发送确认卡片失败: code={} msg={}", response.code, response.msg)
        except Exception as e:
            logger.error("发送确认卡片出错: {}", e)

    async def _upload_and_send_outputs(
        self,
        reply_to: str,
        reply_msg_id: str | None,
        since: float,
    ) -> None:
        """扫描 output 目录，上传本次查询期间产生的文件并发送给用户。

        Args:
            reply_to: 目标 ID（chat_id 或 open_id）
            reply_msg_id: 用于 thread 回复的原始消息 ID
            since: 只上传 mtime >= since 的文件（避免发送旧文件）
        """
        if not self._output_dir or not self._client:
            return
        if not self._output_dir.is_dir():
            return

        # 按修改时间升序，只取本次查询之后的文件
        new_files = sorted(
            (f for f in self._output_dir.iterdir() if f.is_file() and f.stat().st_mtime >= since),
            key=lambda f: f.stat().st_mtime,
        )
        if not new_files:
            return

        for path in new_files:
            try:
                result = await self._upload_file(path)
                if result is None:
                    continue
                feishu_msg_type, feishu_content = result
                await self._send_file_message(
                    reply_to, reply_msg_id, feishu_msg_type, feishu_content
                )
                path.unlink(missing_ok=True)
                logger.info("文件已发送并清理: {}", path.name)
            except Exception as e:
                logger.error("上传/发送文件失败: {} {}", path.name, e)

    async def _upload_file(self, path: Path) -> tuple[str, str] | None:
        """异步上传单个文件，返回 (msg_type, content_json)。"""
        ext = path.suffix.lower()

        if ext in _IMAGE_EXTS:
            from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

            with open(path, "rb") as f:
                request = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder().image_type("message").image(f).build()
                    )
                    .build()
                )
            response = await self._client.im.v1.image.acreate(request)
            if not response.success():
                logger.error("上传图片失败: code={} msg={}", response.code, response.msg)
                return None
            return "image", json.dumps({"image_key": response.data.image_key}, ensure_ascii=False)

        else:
            file_type = _EXT_TO_FILE_TYPE.get(ext, "stream")
            from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

            with open(path, "rb") as f:
                request = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(path.name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
            response = await self._client.im.v1.file.acreate(request)
            if not response.success():
                logger.error("上传文件失败: code={} msg={}", response.code, response.msg)
                return None
            return "file", json.dumps({"file_key": response.data.file_key}, ensure_ascii=False)

    async def _send_file_message(
        self,
        target: str,
        reply_to_message_id: str | None,
        feishu_msg_type: str,
        feishu_content: str,
    ) -> None:
        """发送文件/图片消息（thread 回复或直接发送）。"""
        if reply_to_message_id:
            from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

            request = (
                ReplyMessageRequest.builder()
                .message_id(reply_to_message_id)
                .request_body(
                    ReplyMessageRequestBody.builder()
                    .content(feishu_content)
                    .msg_type(feishu_msg_type)
                    .reply_in_thread(True)
                    .build()
                )
                .build()
            )
            response = await self._client.im.v1.message.areply(request)
        else:
            from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

            receive_id_type = "chat_id" if target.startswith("oc_") else "open_id"
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(target)
                    .msg_type(feishu_msg_type)
                    .content(feishu_content)
                    .build()
                )
                .build()
            )
            response = await self._client.im.v1.message.acreate(request)

        if not response.success():
            logger.error("发送文件消息失败: code={} msg={}", response.code, response.msg)

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


# ── 模块级辅助函数 ──

_RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_RE_TABLE = re.compile(r"\|.+\|[\r\n]+\|[-:| ]+\|")
_CONFIRM_RE = re.compile(r"<<<CONFIRM:\s*(.+?)>>>", re.DOTALL)


def _parse_confirm(confirm_str: str) -> tuple[str, list[str]]:
    """解析 <<<CONFIRM: question | opt1 | opt2 | ...>>> 格式。

    Returns:
        (question, options) 其中 options 至少含 2 项（默认为 ["确认", "取消"]）
    """
    parts = [p.strip() for p in confirm_str.split("|")]
    question = parts[0] if parts else "请确认"
    options = [p for p in parts[1:] if p]
    if not options:
        options = ["确认", "取消"]
    return question, options


def _should_use_card(text: str) -> bool:
    """检测内容是否包含代码块或表格，需要卡片渲染。"""
    return bool(_RE_CODE_BLOCK.search(text) or _RE_TABLE.search(text))


def _split_content(text: str, max_len: int = 3000) -> list[str]:
    """将长文本分割为多段，优先在段落边界切割，不在代码块中间切割。

    Args:
        text: 原始文本
        max_len: 每段最大字符数（默认 3000，飞书消息限制约 4000）

    Returns:
        分割后的文本列表；超过一段时在每段末尾附加 `[n/total]` 页码。
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > max_len:
        pos = _find_split_pos(remaining, max_len)
        if pos <= 0:
            # 找不到合适位置时硬切
            chunks.append(remaining[:max_len])
            remaining = remaining[max_len:]
        else:
            chunks.append(remaining[:pos])
            remaining = remaining[pos:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    # 多段时附加页码
    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"{c}\n\n`[{i + 1}/{total}]`" for i, c in enumerate(chunks)]

    return chunks


def _find_split_pos(text: str, max_len: int) -> int:
    """在 max_len 内找最后一个安全的段落切割点（不在代码块内）。

    优先找 \\n\\n（段落分隔），其次找 \\n（行分隔）。
    代码块（```...```）内部不切割。
    """
    in_code = False
    last_para = -1
    last_line = -1
    i = 0

    while i < max_len and i < len(text):
        # 检测代码块开关
        if text[i : i + 3] == "```":
            in_code = not in_code
            i += 3
            continue

        if not in_code:
            if text[i : i + 2] == "\n\n":
                last_para = i
            elif text[i] == "\n":
                last_line = i

        i += 1

    if last_para > 0:
        return last_para
    if last_line > 0:
        return last_line
    return -1
