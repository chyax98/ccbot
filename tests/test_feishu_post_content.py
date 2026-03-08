"""Tests for extract_post_content (parser module)."""

from ccbot.channels.feishu.parser import extract_post_content


def test_extract_post_content_supports_post_wrapper_shape() -> None:
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

    text = extract_post_content(payload)

    # 只提取 text/a/at 标签的内容，不包含 title
    assert text == "完成"


def test_extract_post_content_keeps_direct_shape_behavior() -> None:
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

    text = extract_post_content(payload)

    assert text == "report"


def test_extract_post_content_handles_at_tag() -> None:
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

    text = extract_post_content(payload)

    assert text == "Hello @Alice"


def test_extract_post_content_empty_when_no_recognized_tags() -> None:
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

    text = extract_post_content(payload)

    assert text == ""
