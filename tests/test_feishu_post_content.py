"""Tests for FeishuChannel._extract_post_content."""

from typing import ClassVar

from ccbot.channels.feishu.adapter import FeishuChannel


class MockConfig:
    """Mock config for testing."""

    app_id = "test"
    app_secret = "test"
    encrypt_key = ""
    verification_token = ""
    allow_from: ClassVar[list[str]] = ["*"]
    react_emoji = "THUMBSUP"
    require_mention = False
    progress_mode = "edit"


def test_extract_post_content_supports_post_wrapper_shape() -> None:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.config = MockConfig()

    payload = {
        "post": {
            "zh_cn": {
                "title": "日报",
                "content": [
                    [
                        {"tag": "text", "text": "完成"},
                        {"tag": "img", "image_key": "img_1"},
                    ]
                ],
            }
        }
    }

    text = channel._extract_post_content(payload)

    # 新实现只提取 text/a/at 标签的内容，不包含 title
    assert text == "完成"


def test_extract_post_content_keeps_direct_shape_behavior() -> None:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.config = MockConfig()

    # 新实现期望 post wrapper 或直接 content 结构
    payload = {
        "post": {
            "en_us": {
                "title": "Daily",
                "content": [
                    [
                        {"tag": "text", "text": "report"},
                        {"tag": "img", "image_key": "img_a"},
                        {"tag": "img", "image_key": "img_b"},
                    ]
                ],
            }
        }
    }

    text = channel._extract_post_content(payload)

    assert text == "report"


def test_extract_post_content_handles_at_tag() -> None:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.config = MockConfig()

    payload = {
        "post": {
            "zh_cn": {
                "content": [
                    [
                        {"tag": "text", "text": "Hello"},
                        {"tag": "at", "user_name": "Alice"},
                    ]
                ],
            }
        }
    }

    text = channel._extract_post_content(payload)

    assert text == "Hello @Alice"


def test_extract_post_content_empty_when_no_recognized_tags() -> None:
    channel = FeishuChannel.__new__(FeishuChannel)
    channel.config = MockConfig()

    payload = {
        "post": {
            "zh_cn": {
                "content": [
                    [
                        {"tag": "img", "image_key": "img_1"},
                    ]
                ],
            }
        }
    }

    text = channel._extract_post_content(payload)

    assert text == ""
