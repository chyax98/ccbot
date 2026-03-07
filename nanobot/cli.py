"""nanobot CLI 入口。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from nanobot import __logo__, __version__

app = typer.Typer(
    name="nanobot",
    help="🐈 nanobot: 基于 Claude Agent SDK 的轻量级个人 AI 助手",
    rich_markup_mode="rich",
)
console = Console()

_DEFAULT_CONFIG = Path.home() / ".nanobot" / "config.json"


def _setup_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if verbose else "INFO",
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )


@app.callback()
def _callback(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="启用详细日志")] = False,
) -> None:
    _setup_logging(verbose)


@app.command()
def version() -> None:
    """显示版本信息。"""
    console.print(
        Panel.fit(
            f"{__logo__} nanobot v{__version__}\nPowered by Claude Agent SDK",
            title="版本信息",
            border_style="cyan",
        )
    )


@app.command()
def chat(
    message: Annotated[str | None, typer.Option("--message", "-m", help="单次消息")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace", "-w", help="workspace 路径")] = None,
) -> None:
    """与 Claude Agent SDK 直接对话（交互模式或单次查询）。"""
    from nanobot.agent import NanobotAgent
    from nanobot.config import AgentConfig
    from nanobot.workspace import WorkspaceManager

    ws_path = Path(workspace) if workspace else Path.home() / ".nanobot" / "workspace"
    ws = WorkspaceManager(ws_path)
    agent = NanobotAgent(AgentConfig(workspace=str(ws_path)), ws)

    async def run() -> None:
        if message:
            console.print(f"[bold]你:[/bold] {message}")
            reply = await agent.ask("cli", message)
            console.print(Markdown(reply))
        else:
            console.print(
                Panel(
                    f"{__logo__} nanobot 交互模式\n输入 [bold]exit[/bold] 退出，[bold]/help[/bold] 查看命令",
                    border_style="green",
                )
            )
            while True:
                try:
                    user_input = console.input("[bold green]你> [/bold green]").strip()
                    if not user_input:
                        continue
                    if user_input.lower() in ("exit", "quit", ":q"):
                        console.print(f"{__logo__} 再见！")
                        break

                    with console.status("[cyan]思考中...[/cyan]", spinner="dots"):
                        reply = await agent.ask("cli", user_input)

                    console.print("[bold blue]nanobot>[/bold blue] ", end="")
                    console.print(Markdown(reply))
                    console.print()

                except KeyboardInterrupt:
                    console.print(f"\n{__logo__} 再见！")
                    break
                except Exception as e:
                    console.print(f"[red]错误: {e}[/red]")

    asyncio.run(run())


@app.command()
def run(
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径"),
    ] = _DEFAULT_CONFIG,
) -> None:
    """启动飞书机器人（使用 Claude Agent SDK 处理消息）。"""
    from nanobot.agent import NanobotAgent
    from nanobot.config import Config
    from nanobot.feishu import FeishuBot
    from nanobot.heartbeat import HeartbeatService
    from nanobot.workspace import WorkspaceManager

    config = Config(_env_file=str(config_path) if config_path.exists() else None)

    if not config.feishu.app_id or not config.feishu.app_secret:
        console.print("[red]错误: 飞书 App ID 和 App Secret 未配置[/red]")
        console.print("请设置环境变量 NANOBOT_FEISHU__APP_ID 和 NANOBOT_FEISHU__APP_SECRET")
        raise typer.Exit(1)

    workspace = WorkspaceManager(Path(config.agent.workspace))
    agent = NanobotAgent(config.agent, workspace)
    bot = FeishuBot(config.feishu, lambda text, chat_id, sender_id: agent.ask(chat_id, text))

    async def heartbeat_execute(prompt: str) -> str:
        return await agent.ask("heartbeat", prompt)

    async def heartbeat_notify(content: str) -> None:
        if agent.last_chat_id:
            await bot.send(agent.last_chat_id, content)

    console.print(
        Panel(
            f"{__logo__} 启动飞书机器人 (Claude Agent SDK)\n"
            f"App ID: [cyan]{config.feishu.app_id[:10]}...[/cyan]\n"
            f"Workspace: [cyan]{workspace.path}[/cyan]",
            border_style="cyan",
        )
    )

    async def main() -> None:
        if config.agent.heartbeat_enabled:
            heartbeat = HeartbeatService(
                workspace.heartbeat_file,
                heartbeat_execute,
                heartbeat_notify,
                interval_s=config.agent.heartbeat_interval,
            )
            await heartbeat.start()
        await bot.start()

    asyncio.run(main())
