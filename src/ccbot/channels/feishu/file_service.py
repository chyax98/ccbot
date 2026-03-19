"""飞书文件上传服务。

处理 workspace/output/ 目录中文件的上传和发送。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

# 图片扩展名 → 走 im.v1.image 上传
IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# 文件扩展名 → Feishu file_type 值（其余用 "stream"）
EXT_TO_FILE_TYPE: dict[str, str] = {
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


async def upload_file(client: Any, path: Path) -> tuple[str, str] | None:
    """异步上传单个文件，返回 (msg_type, content_json)。

    Args:
        client: Lark SDK client
        path: 本地文件路径

    Returns:
        (msg_type, content_json) 或 None
    """
    ext = path.suffix.lower()

    if ext in IMAGE_EXTS:
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        with open(path, "rb") as f:
            request = (
                CreateImageRequest.builder()
                .request_body(
                    CreateImageRequestBody.builder().image_type("message").image(f).build()
                )
                .build()
            )
            response = await client.im.v1.image.acreate(request)
        if not response.success():
            logger.error("上传图片失败: code={} msg={}", response.code, response.msg)
            return None
        return "image", json.dumps({"image_key": response.data.image_key}, ensure_ascii=False)

    else:
        file_type = EXT_TO_FILE_TYPE.get(ext, "stream")
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
            response = await client.im.v1.file.acreate(request)
        if not response.success():
            logger.error("上传文件失败: code={} msg={}", response.code, response.msg)
            return None
        return "file", json.dumps({"file_key": response.data.file_key}, ensure_ascii=False)


async def upload_and_send_outputs(
    client: Any,
    output_dir: Path | None,
    reply_to: str,
    reply_msg_id: str | None,
    since: float,
    send_file_fn: Any,
) -> None:
    """扫描 output 目录，上传本次查询期间产生的文件并发送给用户。

    Args:
        client: Lark SDK client
        output_dir: output 目录路径
        reply_to: 回复目标 ID
        reply_msg_id: 原始消息 ID（用于 thread 回复）
        since: 只上传 mtime >= since 的文件
        send_file_fn: 发送文件消息的异步函数
    """
    if not output_dir or not client:
        return
    if not output_dir.is_dir():
        return

    new_files = sorted(
        (f for f in output_dir.iterdir() if f.is_file() and f.stat().st_mtime >= since),
        key=lambda f: f.stat().st_mtime,
    )
    if not new_files:
        return

    for path in new_files:
        try:
            result = await upload_file(client, path)
            if result is None:
                continue
            feishu_msg_type, feishu_content = result
            await send_file_fn(client, reply_to, reply_msg_id, feishu_msg_type, feishu_content)
            path.unlink(missing_ok=True)
            logger.info("文件已发送并清理: {}", path.name)
        except Exception as e:
            logger.error("上传/发送文件失败: {} {}", path.name, e)
