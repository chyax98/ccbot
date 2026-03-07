"""Feishu channel adapter."""

try:
    from ccbot.channels.feishu.adapter import FeishuChannel

    __all__ = ["FeishuChannel"]
except ImportError:
    # lark-oapi not installed
    __all__ = []
