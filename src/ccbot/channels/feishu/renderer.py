"""飞书消息渲染与发送模块。

负责消息格式渲染（卡片/post）、长消息分段、确认卡片、文件消息发送。
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

# ── 正则常量 ──

_RE_CODE_BLOCK = re.compile(r"```[\s\S]*?```")
_RE_TABLE = re.compile(r"\|.+\|[\r\n]+\|[-:| ]+\|")
CONFIRM_RE = re.compile(r"<<<CONFIRM:\s*(.+?)>>>", re.DOTALL)


def parse_confirm(confirm_str: str) -> tuple[str, list[str]]:
    """解析 <<<CONFIRM: question | opt1 | opt2 | ...>>> 格式。

    Returns:
        (question, options) 其中 options 至少含 2 项
    """
    parts = [p.strip() for p in confirm_str.split("|")]
    question = parts[0] if parts else "请确认"
    options = [p for p in parts[1:] if p]
    if not options:
        options = ["确认", "取消"]
    return question, options


def should_use_card(text: str) -> bool:
    """检测内容是否包含代码块或表格，需要卡片渲染。"""
    return bool(_RE_CODE_BLOCK.search(text) or _RE_TABLE.search(text))


def split_content(text: str, max_len: int = 3000) -> list[str]:
    """将长文本分割为多段，优先在段落边界切割，不在代码块中间切割。

    Args:
        text: 原始文本
        max_len: 每段最大字符数

    Returns:
        分割后的文本列表；超过一段时附加页码。
    """
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > max_len:
        pos = _find_split_pos(remaining, max_len)
        if pos <= 0:
            chunks.append(remaining[:max_len])
            remaining = remaining[max_len:]
        else:
            chunks.append(remaining[:pos])
            remaining = remaining[pos:].lstrip("\n")

    if remaining:
        chunks.append(remaining)

    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"{c}\n\n`[{i + 1}/{total}]`" for i, c in enumerate(chunks)]

    return chunks


def _find_split_pos(text: str, max_len: int) -> int:
    """在 max_len 内找最后一个安全的段落切割点（不在代码块内）。"""
    in_code = False
    last_para = -1
    last_line = -1
    i = 0

    while i < max_len and i < len(text):
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


async def send_single(
    client: Any,
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

    Args:
        client: Lark SDK client
        target: 目标 ID（chat_id 或 open_id）
        content: 消息内容
        msg_type: 消息类型 - "reply"(默认), "progress"(青色), "error"(红色)
        reply_to_message_id: 原始消息 ID，回复该消息
        reply_in_thread: True 时以话题形式回复
    """
    header_map = {
        "progress": ("⏳ 执行中", "turquoise"),
        "error": ("❌ 出错", "red"),
    }
    use_card = msg_type in header_map or should_use_card(content)

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
            response = await client.im.v1.message.areply(request)
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
            response = await client.im.v1.message.acreate(request)

        if not response.success():
            logger.error("发送失败: code={}, msg={}", response.code, response.msg)

    except Exception as e:
        logger.error("发送消息出错: {}", e)


async def send_confirm_card(
    client: Any,
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
    for i, opt in enumerate(options[:4]):
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
                *buttons,
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
            response = await client.im.v1.message.areply(request)
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
            response = await client.im.v1.message.acreate(request)
        if not response.success():
            logger.error("发送确认卡片失败: code={} msg={}", response.code, response.msg)
    except Exception as e:
        logger.error("发送确认卡片出错: {}", e)


async def send_file_message(
    client: Any,
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
        response = await client.im.v1.message.areply(request)
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
        response = await client.im.v1.message.acreate(request)

    if not response.success():
        logger.error("发送文件消息失败: code={} msg={}", response.code, response.msg)
