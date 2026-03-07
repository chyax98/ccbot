"""飞书机器人（WebSocket 长连接，完整功能）。"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from ccbot.config import FeishuConfig

FEISHU_AVAILABLE = importlib.util.find_spec("lark_oapi") is not None

MSG_TYPE_MAP = {
    "image": "[image]",
    "audio": "[audio]",
    "file": "[file]",
    "sticker": "[sticker]",
}


def _extract_share_card_content(content_json: dict, msg_type: str) -> str:
    """Extract text representation from share cards and interactive messages."""
    parts = []

    if msg_type == "share_chat":
        parts.append(f"[shared chat: {content_json.get('chat_id', '')}]")
    elif msg_type == "share_user":
        parts.append(f"[shared user: {content_json.get('user_id', '')}]")
    elif msg_type == "interactive":
        parts.extend(_extract_interactive_content(content_json))
    elif msg_type == "share_calendar_event":
        parts.append(f"[shared calendar event: {content_json.get('event_key', '')}]")
    elif msg_type == "system":
        parts.append("[system message]")
    elif msg_type == "merge_forward":
        parts.append("[merged forward messages]")

    return "\n".join(parts) if parts else f"[{msg_type}]"


def _extract_interactive_content(content: dict) -> list[str]:
    """Recursively extract text and links from interactive card content."""
    parts = []

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return [content] if content.strip() else []

    if not isinstance(content, dict):
        return parts

    if "title" in content:
        title = content["title"]
        if isinstance(title, dict):
            title_content = title.get("content", "") or title.get("text", "")
            if title_content:
                parts.append(f"title: {title_content}")
        elif isinstance(title, str):
            parts.append(f"title: {title}")

    for elements in (
        content.get("elements", []) if isinstance(content.get("elements"), list) else []
    ):
        for element in elements:
            parts.extend(_extract_element_content(element))

    card = content.get("card", {})
    if card:
        parts.extend(_extract_interactive_content(card))

    header = content.get("header", {})
    if header:
        header_title = header.get("title", {})
        if isinstance(header_title, dict):
            header_text = header_title.get("content", "") or header_title.get("text", "")
            if header_text:
                parts.append(f"title: {header_text}")

    return parts


def _extract_element_content(element: dict) -> list[str]:
    """Extract content from a single card element."""
    parts = []

    if not isinstance(element, dict):
        return parts

    tag = element.get("tag", "")

    if tag in ("markdown", "lark_md"):
        content = element.get("content", "")
        if content:
            parts.append(content)

    elif tag == "div":
        text = element.get("text", {})
        if isinstance(text, dict):
            text_content = text.get("content", "") or text.get("text", "")
            if text_content:
                parts.append(text_content)
        elif isinstance(text, str):
            parts.append(text)
        for field in element.get("fields", []):
            if isinstance(field, dict):
                field_text = field.get("text", {})
                if isinstance(field_text, dict):
                    c = field_text.get("content", "")
                    if c:
                        parts.append(c)

    elif tag == "a":
        href = element.get("href", "")
        text = element.get("text", "")
        if href:
            parts.append(f"link: {href}")
        if text:
            parts.append(text)

    elif tag == "button":
        text = element.get("text", {})
        if isinstance(text, dict):
            c = text.get("content", "")
            if c:
                parts.append(c)
        url = element.get("url", "") or element.get("multi_url", {}).get("url", "")
        if url:
            parts.append(f"link: {url}")

    elif tag == "img":
        alt = element.get("alt", {})
        parts.append(alt.get("content", "[image]") if isinstance(alt, dict) else "[image]")

    elif tag == "note":
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    elif tag == "column_set":
        for col in element.get("columns", []):
            for ce in col.get("elements", []):
                parts.extend(_extract_element_content(ce))

    elif tag == "plain_text":
        content = element.get("content", "")
        if content:
            parts.append(content)

    else:
        for ne in element.get("elements", []):
            parts.extend(_extract_element_content(ne))

    return parts


def _extract_post_content(content_json: dict) -> tuple[str, list[str]]:
    """Extract text and image keys from Feishu post (rich text) message."""

    def _parse_block(block: dict) -> tuple[str | None, list[str]]:
        if not isinstance(block, dict) or not isinstance(block.get("content"), list):
            return None, []
        texts, images = [], []
        if title := block.get("title"):
            texts.append(title)
        for row in block["content"]:
            if not isinstance(row, list):
                continue
            for el in row:
                if not isinstance(el, dict):
                    continue
                tag = el.get("tag")
                if tag in ("text", "a"):
                    texts.append(el.get("text", ""))
                elif tag == "at":
                    texts.append(f"@{el.get('user_name', 'user')}")
                elif tag == "img" and (key := el.get("image_key")):
                    images.append(key)
        return (" ".join(texts).strip() or None), images

    root = content_json
    if isinstance(root, dict) and isinstance(root.get("post"), dict):
        root = root["post"]
    if not isinstance(root, dict):
        return "", []

    if "content" in root:
        text, imgs = _parse_block(root)
        if text or imgs:
            return text or "", imgs

    for key in ("zh_cn", "en_us", "ja_jp"):
        if key in root:
            text, imgs = _parse_block(root[key])
            if text or imgs:
                return text or "", imgs
    for val in root.values():
        if isinstance(val, dict):
            text, imgs = _parse_block(val)
            if text or imgs:
                return text or "", imgs

    return "", []


class FeishuBot:
    """
    飞书机器人，使用 WebSocket 长连接接收消息。

    不需要公网 IP，通过 WebSocket 接收飞书事件。

    Args:
        config: 飞书配置。
        on_message: 消息处理回调，签名为 (text, chat_id, sender_id) -> str。
    """

    _TABLE_RE = re.compile(
        r"((?:^[ \t]*\|.+\|[ \t]*\n)(?:^[ \t]*\|[-:\s|]+\|[ \t]*\n)(?:^[ \t]*\|.+\|[ \t]*\n?)+)",
        re.MULTILINE,
    )
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    _CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```)", re.MULTILINE)
    _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tiff", ".tif"}
    _AUDIO_EXTS = {".opus"}
    _FILE_TYPE_MAP = {
        ".opus": "opus",
        ".mp4": "mp4",
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "doc",
        ".xls": "xls",
        ".xlsx": "xls",
        ".ppt": "ppt",
        ".pptx": "ppt",
    }

    def __init__(
        self,
        config: FeishuConfig,
        on_message: Callable[[str, str, str, Callable[[str], Awaitable[None]]], Awaitable[str]],
    ) -> None:
        self.config = config
        self._on_message_cb = on_message
        self._client: Any = None
        self._ws_client: Any = None
        self._ws_thread: threading.Thread | None = None
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._bot_open_id: str = ""  # 启动后从飞书 API 获取

    async def _fetch_bot_open_id(self) -> None:
        """从飞书 API 获取 bot 自身的 open_id，用于 @mention 精准匹配。"""
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
                if data.get("code") == 0:
                    return data.get("bot", {}).get("open_id", "")
                logger.warning(
                    "获取 bot open_id 失败: code={} msg={}", data.get("code"), data.get("msg")
                )
                return ""

            loop = asyncio.get_running_loop()
            self._bot_open_id = await loop.run_in_executor(None, _get)
            if self._bot_open_id:
                logger.info("Bot open_id: {}", self._bot_open_id)
        except Exception as e:
            logger.warning("获取 bot open_id 出错: {}", e)

    def _is_bot_mentioned(self, message: Any) -> bool:
        """检查消息中是否 @了本 bot 或 @all。"""
        mentions = getattr(message, "mentions", None) or []
        if not mentions:
            return False
        for m in mentions:
            uid = getattr(m, "id", None)
            if not uid:
                continue
            if getattr(uid, "open_id", "") == self._bot_open_id:
                return True  # @bot
            if getattr(uid, "user_id", "") == "all":
                return True  # @all
        return False

    def _is_allowed(self, sender_id: str) -> bool:
        """检查发送者是否在白名单内。"""
        allow_list = self.config.allow_from
        if not allow_list:
            logger.warning("feishu: allow_from 为空，拒绝所有访问")
            return False
        if "*" in allow_list:
            return True
        return sender_id in allow_list

    async def start(self) -> None:
        """启动飞书机器人（阻塞直到停止）。"""
        if not FEISHU_AVAILABLE:
            logger.error("飞书 SDK 未安装，请运行: uv pip install lark-oapi")
            return

        if not self.config.app_id or not self.config.app_secret:
            logger.error("飞书 app_id 和 app_secret 未配置")
            return

        import lark_oapi as lark

        self._running = True
        self._loop = asyncio.get_running_loop()

        self._client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

        # 获取 bot 自身 open_id（用于 require_mention 时精准匹配 @bot）
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
                            logger.info("Feishu WebSocket 重连 (第 {} 次)...", attempt)
                        self._ws_client.start()
                    except Exception as e:
                        logger.warning("Feishu WebSocket 断开: {}", e)
                    if self._running:
                        attempt += 1
                        time.sleep(2)
            finally:
                ws_loop.close()

        self._ws_thread = threading.Thread(target=run_ws, daemon=True)
        self._ws_thread.start()

        logger.info("飞书机器人已启动（WebSocket 长连接）")

        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """停止飞书机器人。"""
        self._running = False
        logger.info("飞书机器人已停止")

    # --- 表情反应 ---

    def _add_reaction_sync(self, message_id: str, emoji_type: str) -> None:
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
            response = self._client.im.v1.message_reaction.create(request)
            if not response.success():
                logger.warning("添加表情失败: code={}, msg={}", response.code, response.msg)
        except Exception as e:
            logger.warning("添加表情出错: {}", e)

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        if not self._client:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._add_reaction_sync, message_id, emoji_type)

    # --- 卡片格式化 ---

    @staticmethod
    def _parse_md_table(table_text: str) -> dict | None:
        lines = [_line.strip() for _line in table_text.strip().split("\n") if _line.strip()]
        if len(lines) < 3:
            return None

        def split(_line: str) -> list[str]:
            return [c.strip() for c in _line.strip("|").split("|")]

        headers = split(lines[0])
        rows = [split(_line) for _line in lines[2:]]
        columns = [
            {"tag": "column", "name": f"c{i}", "display_name": h, "width": "auto"}
            for i, h in enumerate(headers)
        ]
        return {
            "tag": "table",
            "page_size": len(rows) + 1,
            "columns": columns,
            "rows": [
                {f"c{i}": r[i] if i < len(r) else "" for i in range(len(headers))} for r in rows
            ],
        }

    def _build_card_elements(self, content: str) -> list[dict]:
        elements, last_end = [], 0
        for m in self._TABLE_RE.finditer(content):
            before = content[last_end : m.start()]
            if before.strip():
                elements.extend(self._split_headings(before))
            elements.append(
                self._parse_md_table(m.group(1)) or {"tag": "markdown", "content": m.group(1)}
            )
            last_end = m.end()
        remaining = content[last_end:]
        if remaining.strip():
            elements.extend(self._split_headings(remaining))
        return elements or [{"tag": "markdown", "content": content}]

    @staticmethod
    def _split_elements_by_table_limit(
        elements: list[dict], max_tables: int = 1
    ) -> list[list[dict]]:
        """每张卡片最多 max_tables 个表格（API 限制 11310）。"""
        if not elements:
            return [[]]
        groups: list[list[dict]] = []
        current: list[dict] = []
        table_count = 0
        for el in elements:
            if el.get("tag") == "table":
                if table_count >= max_tables:
                    if current:
                        groups.append(current)
                    current = []
                    table_count = 0
                current.append(el)
                table_count += 1
            else:
                current.append(el)
        if current:
            groups.append(current)
        return groups or [[]]

    def _split_headings(self, content: str) -> list[dict]:
        protected = content
        code_blocks = []
        for m in self._CODE_BLOCK_RE.finditer(content):
            code_blocks.append(m.group(1))
            protected = protected.replace(m.group(1), f"\x00CODE{len(code_blocks) - 1}\x00", 1)

        elements = []
        last_end = 0
        for m in self._HEADING_RE.finditer(protected):
            before = protected[last_end : m.start()].strip()
            if before:
                elements.append({"tag": "markdown", "content": before})
            text = m.group(2).strip()
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{text}**"},
                }
            )
            last_end = m.end()
        remaining = protected[last_end:].strip()
        if remaining:
            elements.append({"tag": "markdown", "content": remaining})

        for i, cb in enumerate(code_blocks):
            for el in elements:
                if el.get("tag") == "markdown":
                    el["content"] = el["content"].replace(f"\x00CODE{i}\x00", cb)

        return elements or [{"tag": "markdown", "content": content}]

    # --- 媒体上传/下载 ---

    def _upload_image_sync(self, file_path: str) -> str | None:
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        try:
            with open(file_path, "rb") as f:
                request = (
                    CreateImageRequest.builder()
                    .request_body(
                        CreateImageRequestBody.builder().image_type("message").image(f).build()
                    )
                    .build()
                )
                response = self._client.im.v1.image.create(request)
                if response.success():
                    logger.debug(
                        "上传图片 {}: {}", os.path.basename(file_path), response.data.image_key
                    )
                    return response.data.image_key
                logger.error("上传图片失败: code={}, msg={}", response.code, response.msg)
                return None
        except Exception as e:
            logger.error("上传图片出错 {}: {}", file_path, e)
            return None

    def _upload_file_sync(self, file_path: str) -> str | None:
        from lark_oapi.api.im.v1 import CreateFileRequest, CreateFileRequestBody

        ext = os.path.splitext(file_path)[1].lower()
        file_type = self._FILE_TYPE_MAP.get(ext, "stream")
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "rb") as f:
                request = (
                    CreateFileRequest.builder()
                    .request_body(
                        CreateFileRequestBody.builder()
                        .file_type(file_type)
                        .file_name(file_name)
                        .file(f)
                        .build()
                    )
                    .build()
                )
                response = self._client.im.v1.file.create(request)
                if response.success():
                    logger.debug("上传文件 {}: {}", file_name, response.data.file_key)
                    return response.data.file_key
                logger.error("上传文件失败: code={}, msg={}", response.code, response.msg)
                return None
        except Exception as e:
            logger.error("上传文件出错 {}: {}", file_path, e)
            return None

    def _download_image_sync(
        self, message_id: str, image_key: str
    ) -> tuple[bytes | None, str | None]:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(image_key)
                .type("image")
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                if hasattr(file_data, "read"):
                    file_data = file_data.read()
                return file_data, response.file_name
            logger.error("下载图片失败: code={}, msg={}", response.code, response.msg)
            return None, None
        except Exception as e:
            logger.error("下载图片出错 {}: {}", image_key, e)
            return None, None

    def _download_file_sync(
        self, message_id: str, file_key: str, resource_type: str = "file"
    ) -> tuple[bytes | None, str | None]:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        if resource_type == "audio":
            resource_type = "file"
        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(file_key)
                .type(resource_type)
                .build()
            )
            response = self._client.im.v1.message_resource.get(request)
            if response.success():
                file_data = response.file
                if hasattr(file_data, "read"):
                    file_data = file_data.read()
                return file_data, response.file_name
            logger.error("下载{}失败: code={}, msg={}", resource_type, response.code, response.msg)
            return None, None
        except Exception:
            logger.exception("下载 {} {} 出错", resource_type, file_key)
            return None, None

    async def _download_and_save_media(
        self,
        msg_type: str,
        content_json: dict,
        message_id: str | None = None,
    ) -> tuple[str | None, str]:
        loop = asyncio.get_running_loop()
        media_dir = Path.home() / ".nanobot" / "media"
        media_dir.mkdir(parents=True, exist_ok=True)

        data, filename = None, None

        if msg_type == "image":
            image_key = content_json.get("image_key")
            if image_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_image_sync, message_id, image_key
                )
                if not filename:
                    filename = f"{image_key[:16]}.jpg"

        elif msg_type in ("audio", "file", "media"):
            file_key = content_json.get("file_key")
            if file_key and message_id:
                data, filename = await loop.run_in_executor(
                    None, self._download_file_sync, message_id, file_key, msg_type
                )
                if not filename:
                    ext = {"audio": ".opus", "media": ".mp4"}.get(msg_type, "")
                    filename = f"{file_key[:16]}{ext}"

        if data and filename:
            file_path = media_dir / filename
            file_path.write_bytes(data)
            logger.debug("已下载 {} 到 {}", msg_type, file_path)
            return str(file_path), f"[{msg_type}: {filename}]"

        return None, f"[{msg_type}: 下载失败]"

    # --- 发送消息 ---

    def _send_message_sync(
        self, receive_id_type: str, receive_id: str, msg_type: str, content: str
    ) -> str | None:
        """发送消息，返回 message_id（失败返回 None）。"""
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

        try:
            request = (
                CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type(msg_type)
                    .content(content)
                    .build()
                )
                .build()
            )
            response = self._client.im.v1.message.create(request)
            if not response.success():
                logger.error(
                    "发送{}消息失败: code={}, msg={}, log_id={}",
                    msg_type,
                    response.code,
                    response.msg,
                    response.get_log_id(),
                )
                return None
            msg_id = (
                response.data.message_id if (response.data and response.data.message_id) else None
            )
            logger.debug("已发送{}消息到 {} (id={})", msg_type, receive_id, msg_id)
            return msg_id
        except Exception as e:
            logger.error("发送{}消息出错: {}", msg_type, e)
            return None

    def _patch_message_sync(self, message_id: str, content: str) -> bool:
        """就地更新（PATCH）已发送的 interactive 消息内容。"""
        from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

        try:
            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(PatchMessageRequestBody.builder().content(content).build())
                .build()
            )
            response = self._client.im.v1.message.patch(request)
            if not response.success():
                logger.warning("更新消息失败: code={}, msg={}", response.code, response.msg)
                return False
            return True
        except Exception as e:
            logger.warning("更新消息出错: {}", e)
            return False

    async def send(self, chat_id: str, content: str, media: list[str] | None = None) -> None:
        """发送消息（含媒体文件）到指定 chat_id。"""
        if not self._client:
            logger.warning("飞书客户端未初始化")
            return

        try:
            receive_id_type = "chat_id" if chat_id.startswith("oc_") else "open_id"
            loop = asyncio.get_running_loop()

            for file_path in media or []:
                if not os.path.isfile(file_path):
                    logger.warning("媒体文件不存在: {}", file_path)
                    continue
                ext = os.path.splitext(file_path)[1].lower()
                if ext in self._IMAGE_EXTS:
                    key = await loop.run_in_executor(None, self._upload_image_sync, file_path)
                    if key:
                        await loop.run_in_executor(
                            None,
                            self._send_message_sync,
                            receive_id_type,
                            chat_id,
                            "image",
                            json.dumps({"image_key": key}, ensure_ascii=False),
                        )
                else:
                    key = await loop.run_in_executor(None, self._upload_file_sync, file_path)
                    if key:
                        media_type = "audio" if ext in self._AUDIO_EXTS else "file"
                        await loop.run_in_executor(
                            None,
                            self._send_message_sync,
                            receive_id_type,
                            chat_id,
                            media_type,
                            json.dumps({"file_key": key}, ensure_ascii=False),
                        )

            if content and content.strip():
                elements = self._build_card_elements(content)
                for chunk in self._split_elements_by_table_limit(elements):
                    card = {"config": {"wide_screen_mode": True}, "elements": chunk}
                    await loop.run_in_executor(
                        None,
                        self._send_message_sync,
                        receive_id_type,
                        chat_id,
                        "interactive",
                        json.dumps(card, ensure_ascii=False),
                    )

        except Exception as e:
            logger.error("发送消息出错: {}", e)

    async def _send_thinking_card(self, reply_to: str) -> str | None:
        """发送"处理中"占位卡片，返回 message_id 供后续 PATCH。"""
        receive_id_type = "chat_id" if reply_to.startswith("oc_") else "open_id"
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [{"tag": "markdown", "content": "🤔 正在处理中，请稍候..."}],
        }
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            self._send_message_sync,
            receive_id_type,
            reply_to,
            "interactive",
            json.dumps(card, ensure_ascii=False),
        )

    async def _patch_reply(self, thinking_msg_id: str, reply_to: str, content: str) -> None:
        """将 thinking 卡片就地替换为最终回复；若需多张卡片则额外发送后续卡片。"""
        elements = self._build_card_elements(content) if content.strip() else []
        chunks = (
            self._split_elements_by_table_limit(elements)
            if elements
            else [[{"tag": "markdown", "content": "（无响应）"}]]
        )
        loop = asyncio.get_running_loop()
        receive_id_type = "chat_id" if reply_to.startswith("oc_") else "open_id"
        # PATCH 已有的 thinking 消息为第一张回复卡片
        first_card = {"config": {"wide_screen_mode": True}, "elements": chunks[0]}
        await loop.run_in_executor(
            None,
            self._patch_message_sync,
            thinking_msg_id,
            json.dumps(first_card, ensure_ascii=False),
        )
        # 若回复被分割为多张卡片，后续卡片作为新消息发出
        for chunk in chunks[1:]:
            card = {"config": {"wide_screen_mode": True}, "elements": chunk}
            await loop.run_in_executor(
                None,
                self._send_message_sync,
                receive_id_type,
                reply_to,
                "interactive",
                json.dumps(card, ensure_ascii=False),
            )

    # --- 接收消息 ---

    def _on_message_sync(self, data: Any) -> None:
        """从 WebSocket 线程调度到主事件循环。"""
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._on_message(data), self._loop)

    async def _on_message(self, data: Any) -> None:
        """处理来自飞书的入站消息。"""
        try:
            event = data.event
            message = event.message
            sender = event.sender

            message_id = message.message_id
            if message_id in self._processed_message_ids:
                return
            self._processed_message_ids[message_id] = None
            while len(self._processed_message_ids) > 1000:
                self._processed_message_ids.popitem(last=False)

            if sender.sender_type == "bot":
                return

            sender_id = sender.sender_id.open_id if sender.sender_id else "unknown"
            chat_id = message.chat_id
            chat_type = message.chat_type
            msg_type = message.message_type

            # --- 策略检查 ---
            if chat_type == "p2p":
                # 私聊：pairing 模式只允许白名单用户
                if self.config.dm_policy == "pairing" and not self._is_allowed(sender_id):
                    logger.debug("DM pairing 拒绝: sender_id={}", sender_id)
                    return
            else:
                # 群聊：require_mention 时，只响应 @bot 的消息
                if self.config.require_mention and not self._is_bot_mentioned(message):
                    return

            # 全局白名单（allow_from 非 * 时始终生效）
            if "*" not in self.config.allow_from and not self._is_allowed(sender_id):
                logger.warning("全局白名单拒绝: sender_id={}", sender_id)
                return

            await self._add_reaction(message_id, self.config.react_emoji)

            content_parts: list[str] = []
            media_paths: list[str] = []

            try:
                content_json = json.loads(message.content) if message.content else {}
            except json.JSONDecodeError:
                content_json = {}

            # 群聊 @mention：从文本中去掉 @_user_N 占位符，避免传给 agent
            def _strip_mentions(text: str) -> str:
                if chat_type != "group" or not self.config.require_mention:
                    return text
                for m in getattr(message, "mentions", None) or []:
                    key = getattr(m, "key", "")
                    if key:
                        text = text.replace(key, "")
                return text.strip()

            if msg_type == "text":
                text = _strip_mentions(content_json.get("text", ""))
                if text:
                    content_parts.append(text)

            elif msg_type == "post":
                text, image_keys = _extract_post_content(content_json)
                if text:
                    content_parts.append(text)
                for img_key in image_keys:
                    file_path, content_text = await self._download_and_save_media(
                        "image", {"image_key": img_key}, message_id
                    )
                    if file_path:
                        media_paths.append(file_path)
                    content_parts.append(content_text)

            elif msg_type in ("image", "audio", "file", "media"):
                file_path, content_text = await self._download_and_save_media(
                    msg_type, content_json, message_id
                )
                if file_path:
                    media_paths.append(file_path)
                content_parts.append(content_text)

            elif msg_type in (
                "share_chat",
                "share_user",
                "interactive",
                "share_calendar_event",
                "system",
                "merge_forward",
            ):
                text = _extract_share_card_content(content_json, msg_type)
                if text:
                    content_parts.append(text)

            else:
                content_parts.append(MSG_TYPE_MAP.get(msg_type, f"[{msg_type}]"))

            content = "\n".join(content_parts) if content_parts else ""

            if not content and not media_paths:
                logger.debug("跳过空消息: message_id={} chat_id={}", message_id, chat_id)
                return

            # 将媒体路径附加到文本
            if media_paths:
                content = content + f"\n[附件: {', '.join(media_paths)}]"

            reply_to = chat_id if chat_type == "group" else sender_id

            logger.info("收到来自 {} 的消息: {}", sender_id, content[:60])

            # 先发送"处理中"占位卡片，后续就地更新（避免刷屏）
            thinking_msg_id = await self._send_thinking_card(reply_to)

            # 多 worker 状态看板：{ worker_name: last_tool }
            # 普通消息（无 [name] 前缀）直接显示为单行状态
            _worker_status: dict[str, str] = {}
            _WORKER_PREFIX_RE = re.compile(r"^\[([^\]]+)\]\s*(.*)")
            _MILESTONE_RE = re.compile(r"^(📋|✅|🎯|❌)")  # 关键节点标记

            async def _send_progress(msg: str) -> None:
                # 判断是否为关键节点消息
                is_milestone = bool(_MILESTONE_RE.match(msg))

                # milestone 模式：关键节点发送新消息，工具调用编辑同一条
                if self.config.progress_mode == "milestone" and is_milestone:
                    await self.send(reply_to, msg)
                    return

                # verbose 模式：每条都发送新消息
                if self.config.progress_mode == "verbose":
                    await self.send(reply_to, msg)
                    return

                # edit 模式（默认）：编辑同一条消息
                m = _WORKER_PREFIX_RE.match(msg)
                if m:
                    _worker_status[m.group(1)] = m.group(2)
                    status_lines = "\n".join(f"`[{k}]` {v}" for k, v in _worker_status.items())
                    display = f"{status_lines}\n\n⏳ 处理中，请稍候..."
                else:
                    display = f"{msg}\n\n⏳ 处理中，请稍候..."

                if thinking_msg_id:
                    card = {
                        "config": {"wide_screen_mode": True},
                        "elements": [{"tag": "markdown", "content": display}],
                    }
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(
                        None,
                        self._patch_message_sync,
                        thinking_msg_id,
                        json.dumps(card, ensure_ascii=False),
                    )
                else:
                    await self.send(reply_to, msg)

            try:
                reply = await self._on_message_cb(content, reply_to, sender_id, _send_progress)
                if thinking_msg_id:
                    await self._patch_reply(thinking_msg_id, reply_to, reply)
                else:
                    await self.send(reply_to, reply)
            except Exception as e:
                logger.error("处理消息回调出错: {}", e)
                error_msg = f"抱歉，处理消息时出现错误: {e}"
                if thinking_msg_id:
                    loop = asyncio.get_running_loop()
                    error_card = {
                        "config": {"wide_screen_mode": True},
                        "elements": [{"tag": "markdown", "content": error_msg}],
                    }
                    await loop.run_in_executor(
                        None,
                        self._patch_message_sync,
                        thinking_msg_id,
                        json.dumps(error_card, ensure_ascii=False),
                    )
                else:
                    await self.send(reply_to, error_msg)

        except Exception as e:
            logger.error("处理飞书消息出错: {}", e)
