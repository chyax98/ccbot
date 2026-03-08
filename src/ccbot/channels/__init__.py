"""Channel adapters for different messaging platforms."""

from ccbot.channels.base import Channel, ChannelCapability, ChannelResponder, IncomingMessage

try:
    from ccbot.channels.cli import CLIChannel

    __all__ = ["CLIChannel", "Channel", "ChannelCapability", "ChannelResponder", "IncomingMessage"]
except ImportError:
    __all__ = ["Channel", "ChannelCapability", "ChannelResponder", "IncomingMessage"]
