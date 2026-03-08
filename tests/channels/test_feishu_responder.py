from __future__ import annotations

from pathlib import Path

from ccbot.channels.base import IncomingMessage
from ccbot.channels.feishu.responder import FeishuResponder


class _DummyFeishuChannel:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []
        self._client = object()
        self._output_dir = Path("/tmp/output")

    @property
    def client(self):
        return self._client

    @property
    def output_dir(self):
        return self._output_dir

    async def send(self, target: str, content: str, **kwargs) -> None:
        self.sent.append((target, content, kwargs))


async def test_feishu_responder_uses_thread_reply_semantics() -> None:
    channel = _DummyFeishuChannel()
    message = IncomingMessage(
        text="hello",
        channel="feishu",
        conversation_id="oc_chat",
        reply_target="oc_chat",
        sender_id="ou_user",
        message_id="om_msg",
        thread_id="om_root",
    )
    responder = FeishuResponder(channel, message)

    await responder.reply("final")
    await responder.progress("working")
    await responder.worker_result("worker-a", "ok")
    await responder.error("boom")

    assert channel.sent == [
        (
            "oc_chat",
            "final",
            {"reply_to_message_id": "om_root"},
        ),
        (
            "oc_chat",
            "working",
            {
                "msg_type": "progress",
                "reply_to_message_id": "om_root",
                "reply_in_thread": True,
            },
        ),
        (
            "oc_chat",
            "**✅ [worker-a]**\n\nok",
            {
                "reply_to_message_id": "om_root",
                "reply_in_thread": True,
            },
        ),
        (
            "oc_chat",
            "boom",
            {
                "msg_type": "error",
                "reply_to_message_id": "om_root",
            },
        ),
    ]


async def test_feishu_responder_upload_outputs_since(monkeypatch) -> None:
    channel = _DummyFeishuChannel()
    message = IncomingMessage(
        text="hello",
        channel="feishu",
        conversation_id="oc_chat",
        reply_target="oc_chat",
        sender_id="ou_user",
        message_id="om_msg",
        thread_id="om_root",
    )
    responder = FeishuResponder(channel, message)

    seen = {}

    async def fake_upload(client, output_dir, reply_to, reply_msg_id, since, sender):
        seen["client"] = client
        seen["output_dir"] = output_dir
        seen["reply_to"] = reply_to
        seen["reply_msg_id"] = reply_msg_id
        seen["since"] = since
        seen["sender"] = sender

    monkeypatch.setattr("ccbot.channels.feishu.responder.upload_and_send_outputs", fake_upload)

    await responder.upload_outputs_since(12.5)

    assert seen["client"] is channel.client
    assert seen["output_dir"] == channel.output_dir
    assert seen["reply_to"] == "oc_chat"
    assert seen["reply_msg_id"] == "om_root"
    assert seen["since"] == 12.5
