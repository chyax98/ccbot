"""CLI 通道适配器。

支持交互式命令行和单次消息模式。
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from ccbot.channels.base import Channel


class CLIChannel(Channel):
    """命令行通道，用于本地交互和调试。

    Features:
    - 交互式模式: 持续读取用户输入
    - 单次模式: 处理单条消息并退出
    - 支持 progress 回调显示

    Example:
        # 交互式模式
        cli = CLIChannel()
        cli.on_message(handler)
        await cli.start()

        # 单次模式
        cli = CLIChannel(single_message="Hello")
        cli.on_message(handler)
        await cli.start()
    """

    def __init__(
        self,
        single_message: str | None = None,
        prompt: str = "You: ",
        bot_name: str = "🐈 ccbot",
    ) -> None:
        """初始化 CLI 通道。

        Args:
            single_message: 如果提供，处理该消息后立即退出
            prompt: 交互式提示符
            bot_name: Bot 显示名称
        """
        super().__init__()
        self._single_message = single_message
        self._prompt = prompt
        self._bot_name = bot_name
        self._chat_id = "cli"
        self._sender_id = "user"

    async def start(self) -> None:
        """启动 CLI 通道。"""
        self._running = True
        logger.info("CLI 通道已启动")

        if self._single_message:
            await self._process_single_message(self._single_message)
        else:
            await self._interactive_loop()

    async def stop(self) -> None:
        """停止 CLI 通道。"""
        self._running = False
        logger.info("CLI 通道已停止")

    async def send(self, target: str, content: str, **kwargs: Any) -> None:
        """发送消息到控制台。"""
        # 清除当前行并打印 bot 回复
        print(f"\r{self._bot_name}: {content}")
        if self._running and not self._single_message:
            print(self._prompt, end="", flush=True)

    async def _process_single_message(self, message: str) -> None:
        """处理单条消息。"""
        print(f"You: {message}")

        async def progress_cb(msg: str) -> None:
            print(f"[{msg}]")

        reply = await self._handle_message(message, self._chat_id, self._sender_id, progress_cb)
        print(f"{self._bot_name}: {reply}")

    async def _interactive_loop(self) -> None:
        """交互式循环读取用户输入。"""
        print(f"{self._bot_name} CLI 模式 (输入 /quit 退出)")
        print("-" * 40)

        while self._running:
            try:
                # 使用 run_in_executor 避免阻塞事件循环
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

                # 处理消息
                async def progress_cb(msg: str) -> None:
                    print(f"  ... {msg}")

                reply = await self._handle_message(
                    user_input, self._chat_id, self._sender_id, progress_cb
                )

                # 显示回复
                print(f"{self._bot_name}: {reply}")

            except EOFError:
                # 处理 Ctrl+D
                print("\n再见!")
                await self.stop()
                break
            except KeyboardInterrupt:
                # 处理 Ctrl+C
                print("\n再见!")
                await self.stop()
                break
            except Exception as e:
                logger.exception("CLI 处理失败: {}", e)
                print(f"错误: {e}")

    def _print_help(self) -> None:
        """打印帮助信息。"""
        help_text = f"""
{self._bot_name} 命令:
  /help  - 显示此帮助
  /quit  - 退出程序
  /new   - 开始新会话 (清除上下文)
  /stop  - 停止当前任务
"""
        print(help_text)
