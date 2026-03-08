"""ccbot CLI 入口。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ccbot import __logo__, __version__

if TYPE_CHECKING:
    from ccbot.channels.base import Channel
    from ccbot.config import Config
    from ccbot.workspace import WorkspaceManager

app = typer.Typer(
    name="ccbot",
    help="🐈 ccbot: 基于 Claude Agent SDK 的轻量级个人 AI 助手",
    rich_markup_mode="rich",
)
console = Console()

_DEFAULT_CONFIG = Path.home() / ".ccbot" / "config.json"


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
            f"{__logo__} ccbot v{__version__}\nPowered by Claude Agent SDK",
            title="版本信息",
            border_style="cyan",
        )
    )


@app.command()
def chat(
    message: Annotated[str | None, typer.Option("--message", "-m", help="单次消息")] = None,
    workspace: Annotated[
        str | None, typer.Option("--workspace", "-w", help="workspace 路径")
    ] = None,
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径（JSON）"),
    ] = _DEFAULT_CONFIG,
) -> None:
    """与 Claude Agent SDK 直接对话（交互模式或单次查询，支持多 Agent 调度）。"""
    from ccbot.config import load_config
    from ccbot.team import AgentTeam
    from ccbot.workspace import WorkspaceManager

    config = load_config(config_path)
    ws_path = Path(workspace) if workspace else Path(config.agent.workspace)
    ws = WorkspaceManager(ws_path)
    team = AgentTeam(config.agent, ws)

    async def run() -> None:
        await team.start()
        try:
            if message:
                console.print(f"[bold]你:[/bold] {message}")
                reply = await team.ask("cli", message)
                console.print(Markdown(reply))
            else:
                console.print(
                    Panel(
                        f"{__logo__} ccbot 交互模式\n输入 [bold]exit[/bold] 退出，[bold]/help[/bold] 查看命令",
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
                            reply = await team.ask("cli", user_input)

                        console.print("[bold blue]ccbot>[/bold blue] ", end="")
                        console.print(Markdown(reply))
                        console.print()

                    except KeyboardInterrupt:
                        console.print(f"\n{__logo__} 再见！")
                        break
                    except Exception as e:
                        console.print(f"[red]错误: {e}[/red]")
        finally:
            await team.stop()

    asyncio.run(run())


@app.command()
def worker(
    task: str,
    cwd: Annotated[str, typer.Option("--cwd", "-C", help="工作目录（项目路径）")],
    output: Annotated[str, typer.Option("--output", "-o", help="结果写入路径（.md 文件）")],
    model: Annotated[str, typer.Option("--model", "-m", help="使用的模型")] = "",
    max_turns: Annotated[int, typer.Option("--max-turns", help="最大轮数")] = 30,
) -> None:
    """启动单次 worker agent 执行任务，结果写入文件（备用：供外部脚本调用）。"""
    from ccbot.agent import CCBotAgent
    from ccbot.config import AgentConfig
    from ccbot.runtime.profiles import RuntimeRole

    out_path = Path(output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cwd_resolved = str(Path(cwd).expanduser().resolve())

    cfg = AgentConfig(
        model=model,
        max_turns=max_turns,
        cwd=cwd_resolved,
        system_prompt=(
            f"You are a focused AI coding assistant.\n"
            f"Working directory: {cwd_resolved}\n"
            f"Complete the assigned task thoroughly. When done, write a clear markdown "
            f"summary of what you accomplished to: {out_path}"
        ),
    )
    agent = CCBotAgent(cfg, role=RuntimeRole.WORKER)

    async def run_worker() -> None:
        await agent.start()
        try:
            result = await agent.ask("worker", task)
            out_path.write_text(result, encoding="utf-8")
            logger.info("worker 完成，结果写入: {}", out_path)
        finally:
            await agent.stop()

    asyncio.run(run_worker())


def _create_channel(
    channel_type: str,
    config: Config,
    workspace: WorkspaceManager,
) -> Channel:
    """根据 channel_type 创建对应通道。"""
    if channel_type == "feishu":
        from ccbot.channels.feishu import FeishuChannel

        if not config.feishu.app_id or not config.feishu.app_secret:
            raise typer.BadParameter(
                "飞书 App ID 和 App Secret 未配置，请在配置文件中设置或使用环境变量 "
                "CCBOT_FEISHU__APP_ID / CCBOT_FEISHU__APP_SECRET"
            )
        return FeishuChannel(config.feishu, output_dir=workspace.output_dir)
    raise typer.BadParameter(f"不支持的通道类型: {channel_type}")


@app.command()
def run(
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径（JSON）"),
    ] = _DEFAULT_CONFIG,
    channel_type: Annotated[
        str,
        typer.Option("--channel", help="通道类型（feishu）"),
    ] = "feishu",
) -> None:
    """启动机器人（Supervisor+Worker 多 Agent 模式）。"""
    from ccbot.channels.base import IncomingMessage
    from ccbot.config import load_config
    from ccbot.heartbeat import HeartbeatService
    from ccbot.scheduler import SchedulerService
    from ccbot.team import AgentTeam
    from ccbot.workspace import WorkspaceManager

    config = load_config(config_path)
    workspace = WorkspaceManager(Path(config.agent.workspace))
    channel = _create_channel(channel_type, config, workspace)
    team = AgentTeam(config.agent, workspace)

    async def on_message(
        message: IncomingMessage,
        send_progress,
        send_worker_result,
    ) -> str:
        return await team.ask(
            message.conversation_id,
            message.text,
            on_progress=send_progress,
            on_worker_result=send_worker_result,
            request_context={
                "channel": message.channel,
                "notify_target": message.reply_target,
                "conversation_id": message.conversation_id,
                "sender_id": message.sender_id,
            },
        )

    channel.on_message_context(on_message)

    async def heartbeat_execute(prompt: str) -> str:
        return await team.ask("heartbeat", prompt)

    async def heartbeat_notify(content: str) -> None:
        target = config.agent.heartbeat_notify_chat_id or team.last_chat_id
        if target:
            await channel.send(target, content)
        else:
            logger.warning(
                "心跳通知无目标 chat_id，已跳过（可在配置中设置 heartbeat_notify_chat_id）"
            )

    async def schedule_execute(job) -> str:
        return await team.ask(job.runtime_chat_id, job.prompt)

    async def schedule_notify(job, content: str) -> None:
        target = job.notify_target or config.agent.heartbeat_notify_chat_id or team.last_chat_id
        if target:
            await channel.send(target, content)
        else:
            logger.warning("定时任务通知无目标，已跳过 job_id={}", job.job_id)

    scheduler = SchedulerService(
        workspace.path,
        schedule_execute,
        schedule_notify,
        poll_interval_s=config.agent.scheduler_poll_interval_s,
    )
    team.set_scheduler(scheduler)

    console.print(
        Panel(
            f"{__logo__} 启动 {channel_type} 机器人 (Supervisor+Worker)\n"
            f"Channel: [cyan]{channel_type}[/cyan]\n"
            f"Model:   [cyan]{config.agent.model or 'default'}[/cyan]\n"
            f"Workspace: [cyan]{workspace.path}[/cyan]",
            border_style="cyan",
        )
    )

    async def main() -> None:
        await team.start()
        heartbeat = None
        try:
            if config.agent.scheduler_enabled:
                await scheduler.start()
            if config.agent.heartbeat_enabled:
                heartbeat = HeartbeatService(
                    workspace.heartbeat_file,
                    heartbeat_execute,
                    heartbeat_notify,
                    interval_s=config.agent.heartbeat_interval,
                )
                await heartbeat.start()
            await channel.start()
            await channel.wait_closed()
        finally:
            if heartbeat is not None:
                heartbeat.stop()
            if config.agent.scheduler_enabled:
                await scheduler.stop()
            await channel.stop()
            await team.stop()

    asyncio.run(main())
