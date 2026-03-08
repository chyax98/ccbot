from __future__ import annotations

from ccbot.channels.base import Channel, ChannelCapability, IncomingMessage


class _DummyChannel(Channel):
    @property
    def channel_name(self) -> str:
        return "dummy"

    @property
    def capabilities(self) -> frozenset[ChannelCapability]:
        return frozenset({ChannelCapability.PROGRESS_UPDATES})

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def send(self, target: str, content: str, **kwargs) -> None:
        return None


async def _noop_progress(_: str) -> None:
    return None


async def test_handle_message_supports_legacy_four_arg_handler() -> None:
    channel = _DummyChannel()

    async def handler(content: str, reply_to: str, sender_id: str, progress_cb) -> str:
        assert content == "hello"
        assert reply_to == "chat-1"
        assert sender_id == "user-1"
        assert progress_cb is _noop_progress
        return "ok"

    channel.on_message(handler)
    reply = await channel._handle_message("hello", "chat-1", "user-1", _noop_progress)
    assert reply == "ok"


async def test_handle_message_supports_context_handler() -> None:
    channel = _DummyChannel()

    async def handler(message: IncomingMessage, progress_cb, result_sender) -> str:
        assert message.channel == "dummy"
        assert message.text == "hello"
        assert message.conversation_id == "chat-1"
        assert message.message_id == "msg-1"
        assert message.thread_id == "thread-1"
        assert message.mentions_bot is True
        assert message.metadata["source"] == "test"
        assert result_sender is None
        return "ok"

    channel.on_message_context(handler)
    reply = await channel._handle_message(
        "hello",
        "chat-1",
        "user-1",
        _noop_progress,
        message_id="msg-1",
        thread_id="thread-1",
        mentions_bot=True,
        metadata={"source": "test"},
    )
    assert reply == "ok"


async def test_default_responder_routes_messages_via_channel_send() -> None:
    events: list[tuple[str, str, dict]] = []

    class _RecordingChannel(_DummyChannel):
        async def send(self, target: str, content: str, **kwargs) -> None:
            events.append((target, content, kwargs))

    channel = _RecordingChannel()
    responder = channel.build_responder(
        IncomingMessage(
            text="hello",
            channel="dummy",
            conversation_id="chat-1",
            reply_target="chat-1",
            sender_id="user-1",
        )
    )

    await responder.reply("done")
    await responder.progress("working")
    await responder.worker_result("worker-a", "ok")
    await responder.error("boom")

    assert events == [
        ("chat-1", "done", {}),
        ("chat-1", "working", {}),
        ("chat-1", "**✅ [worker-a]**\n\nok", {}),
        ("chat-1", "boom", {}),
    ]
