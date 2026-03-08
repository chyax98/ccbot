"""CLI 通道适配器。

支持交互式命令行和单次消息模式。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from ccbot.channels.base import Channel, ChannelCapability


class CLIChannel(Channel):
    """命令行通道，用于本地交互和调试。"""

    def __init__(
        self,
        single_message: str | None = None,
        prompt: str = "You: ",
        bot_name: str = "🐈 ccbot",
    ) -> None:
        super().__init__()
        self._single_message = single_message
        self._prompt = prompt
        self._bot_name = bot_name
        self._chat_id = "cli"
        self._sender_id = "user"

    @property
    def channel_name(self) -> str:
        return "cli"

    @property
    def capabilities(self) -> frozenset[ChannelCapability]:
        return frozenset(
            {
                ChannelCapability.PROGRESS_UPDATES,
                ChannelCapability.WORKER_RESULTS,
                ChannelCapability.RICH_TEXT,
            }
        )

    async def start(self) -> None:
        self._running = True
        logger.info("CLI 通道已启动")

        if self._single_message:
            await self._process_single_message(self._single_message)
        else:
            await self._interactive_loop()

    async def stop(self) -> None:
        self._running = False
        logger.info("CLI 通道已停止")

    async def send(self, target: str, content: str, **kwargs: Any) -> None:
        print(f"\r{self._bot_name}: {content}")
        if self._running and not self._single_message:
            print(self._prompt, end="", flush=True)

    async def _process_single_message(self, message: str) -> None:
        print(f"You: {message}")

        async def progress_cb(msg: str) -> None:
            print(f"[{msg}]")

        async def result_cb(worker_name: str, result: str) -> None:
            print(f"[{worker_name}] {result}")

        reply = await self._handle_message(
            message,
            self._chat_id,
            self._sender_id,
            progress_cb,
            result_cb,
        )
        print(f"{self._bot_name}: {reply}")

    async def _interactive_loop(self) -> None:
        print(f"{self._bot_name} CLI 模式 (输入 /quit 退出)")
        print("-" * 40)

        while self._running:
            try:
                loop = asyncio.get_running_loop()
                user_input = await loop.run_in_executor(None, lambda: input(self._prompt))

                user_input = user_input.strip()
                if not user_input:
                    continue

                if user_input.lower() in ("/quit", "/exit", "/q"):
                    print("再见!")
                    await self.stop()
                    break

                if user_input.lower() == "/help":
                    self._print_help()
                    continue

                async def progress_cb(msg: str) -> None:
                    print(f"  ... {msg}")

                async def result_cb(worker_name: str, result: str) -> None:
                    print(f"  => [{worker_name}] {result}")

                reply = await self._handle_message(
                    user_input,
                    self._chat_id,
                    self._sender_id,
                    progress_cb,
                    result_cb,
                )

                print(f"{self._bot_name}: {reply}")
                print()

            except KeyboardInterrupt:
                print("\n再见!")
                await self.stop()
                break
            except EOFError:
                print("\n再见!")
                await self.stop()
                break

    def _print_help(self) -> None:
        print("可用命令:")
        print("  /help        显示帮助")
        print("  /quit        退出")
        print("  /new         新建会话")
        print("  /stop        中断当前任务")
        print("  /workers     查看活跃 Workers")
        print("  /worker stop <name>  中断指定 Worker")
        print("  /worker kill <name>  销毁指定 Worker")
        print("  /memory show         查看本地记忆快照")
        print("  /memory clear        清空当前会话记忆")
        print("  /schedule list       查看定时任务")
        print("  /schedule run <id>   立即执行定时任务")
        print("  /schedule pause <id> 暂停定时任务")
        print("  /schedule resume <id> 恢复定时任务")
        print("  /schedule delete <id> 删除定时任务")
