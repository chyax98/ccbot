"""飞书消息内容提取模块。

从飞书事件中提取各类消息的文本内容，支持文本、富文本、图片、文件等类型。
图片和文件会异步下载到本地临时目录。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from loguru import logger


async def extract_content(event: dict, client: Any = None) -> str:
    """提取消息内容（异步，支持图片/文件下载）。

    Args:
        event: 飞书事件字典
        client: Lark SDK client（图片/文件下载需要）

    Returns:
        提取出的文本内容
    """
    message = event.get("message", {})
    msg_type = message.get("message_type", "")
    content_str = message.get("content", "{}")
    message_id = message.get("message_id", "")

    try:
        content_json = json.loads(content_str)
    except json.JSONDecodeError:
        return ""

    # 使用合并后的内容（Debounce 合并多条消息时）
    if event.get("_merged"):
        return str(event.get("_merged_content", ""))

    if msg_type == "text":
        return str(content_json.get("text", "")).strip()

    if msg_type == "post":
        return extract_post_content(content_json)

    if msg_type == "image":
        image_key = content_json.get("image_key", "")
        if image_key and message_id and client:
            path = await download_resource(client, message_id, image_key, "image")
            if path:
                return f"[图片已下载，路径: {path}，请用 Read 工具查看]"
        return "[图片（下载失败）]"

    if msg_type == "file":
        file_key = content_json.get("file_key", "")
        file_name = content_json.get("file_name", "file")
        if file_key and message_id and client:
            path = await download_resource(client, message_id, file_key, "file", file_name)
            if path:
                return f"[文件 '{file_name}' 已下载，路径: {path}，可用 Read/Bash 工具处理]"
        return f"[文件 '{file_name}'（下载失败）]"

    type_map = {"audio": "[音频]", "sticker": "[表情]"}
    return type_map.get(msg_type, f"[{msg_type}]")


def extract_post_content(content_json: dict) -> str:
    """提取富文本内容。

    Args:
        content_json: 富文本 JSON 结构

    Returns:
        提取出的纯文本
    """
    texts: list[str] = []
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


async def download_resource(
    client: Any,
    message_id: str,
    file_key: str,
    resource_type: str,
    file_name: str = "",
) -> str | None:
    """下载飞书消息资源（图片/文件），返回本地文件路径。

    Args:
        client: Lark SDK client
        message_id: 消息 ID
        file_key: 文件 key
        resource_type: 资源类型（"image" 或 "file"）
        file_name: 文件名（可选）

    Returns:
        本地文件路径，失败返回 None
    """
    if not client:
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
        response = await client.im.v1.message_resource.aget(request)

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
