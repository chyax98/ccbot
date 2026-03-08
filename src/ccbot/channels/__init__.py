"""Channel adapters for different messaging platforms."""

from ccbot.channels.base import Channel

try:
    from ccbot.channels.cli import CLIChannel

    __all__ = ["CLIChannel", "Channel"]
except ImportError:
    __all__ = ["Channel"]
