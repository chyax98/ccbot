"""ccbot CLI 入口。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from ccbot import __logo__, __version__
from ccbot.observability import get_langsmith_status

if TYPE_CHECKING:
    from ccbot.channels.base import Channel
    from ccbot.config import AgentConfig, Config
    from ccbot.scheduler import SchedulerService
    from ccbot.team import AgentTeam
    from ccbot.workspace import WorkspaceManager

app = typer.Typer(
    name="ccbot",
    help="🐈 ccbot: 基于 Claude Agent SDK 的轻量级个人 AI 助手",
    rich_markup_mode="rich",
)
console = Console()

_DEFAULT_CONFIG = Path.home() / ".ccbot" / "config.json"


def _daemonize(pid_file: Path | None = None) -> None:
    """将当前进程转为后台守护进程。

    使用 double-fork 技术确保进程完全脱离终端。
    """
    # First fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 脱离终端
    os.setsid()

    # Second fork
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 重定向标准流
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, sys.stdin.fileno())
    os.dup2(devnull, sys.stdout.fileno())
    os.dup2(devnull, sys.stderr.fileno())

    # 写入 PID 文件
    if pid_file:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(str(os.getpid()))


def _augment_langsmith_metadata(
    config: Config,
    *,
    entrypoint: str,
    workspace: Path | None = None,
    channel_type: str | None = None,
) -> dict[str, object]:
    metadata = dict(config.agent.langsmith_metadata)
    metadata.setdefault("entrypoint", entrypoint)
    if workspace is not None:
        metadata.setdefault("workspace", str(workspace))
    if channel_type:
        metadata.setdefault("channel", channel_type)
    if config.agent.model:
        metadata.setdefault("configured_model", config.agent.model)
    config.agent.langsmith_metadata = metadata
    return get_langsmith_status(config.agent)


def _format_langsmith_status(status: dict[str, object]) -> str:
    if not status.get("enabled"):
        return "disabled"
    project = status.get("project") or "default"
    return f"enabled ({project})"


def _setup_logging(config: "Config", verbose: bool = False) -> None:
    """初始化日志系统。"""
    from ccbot.logging_setup import setup_logging

    logging_config = config.logging.model_copy(deep=True)
    if verbose:
        logging_config.level = "DEBUG"
    setup_logging(logging_config)


@app.callback()
def _callback(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="启用详细日志")] = False,
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径"),
    ] = _DEFAULT_CONFIG,
) -> None:
    from ccbot.config import load_config

    config = load_config(config_path)
    _setup_logging(config, verbose=verbose)


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
def onboard(
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径"),
    ] = _DEFAULT_CONFIG,
) -> None:
    """交互式配置向导，引导完成首次配置。"""
    import json

    from rich.prompt import Confirm, Prompt

    console.print(Panel(f"{__logo__} ccbot 配置向导", border_style="cyan"))

    # 检查已有配置
    if config_path.exists():
        if not Confirm.ask(f"配置文件 {config_path} 已存在，是否覆盖？", default=False):
            console.print("[yellow]已取消[/yellow]")
            return

    # 选择使用场景
    console.print("\n[bold]请选择使用场景:[/bold]")
    console.print("  1) CLI 交互模式 - 仅在终端使用")
    console.print("  2) 飞书机器人   - 接入飞书群聊/私聊")
    mode = Prompt.ask("选择", choices=["1", "2"], default="1")

    config: dict[str, dict[str, object]] = {
        "feishu": {},
        "agent": {},
    }

    # Agent 基础配置
    console.print("\n[bold cyan]── Agent 配置 ──[/bold cyan]")

    model = Prompt.ask(
        "模型名称 (留空使用 SDK 默认)",
        default="",
    )
    if model:
        config["agent"]["model"] = model

    # 飞书配置（如果选择场景2）
    if mode == "2":
        console.print("\n[bold cyan]── 飞书配置 ──[/bold cyan]")
        console.print("[dim]请前往飞书开放平台 https://open.feishu.cn 创建企业自建应用[/dim]\n")

        app_id = Prompt.ask("App ID", default="")
        app_secret = Prompt.ask("App Secret", password=True, default="")

        config["feishu"]["app_id"] = app_id
        config["feishu"]["app_secret"] = app_secret

        # 群聊策略
        require_mention = Confirm.ask(
            "群聊中是否需要 @机器人 才响应？",
            default=True,
        )
        config["feishu"]["require_mention"] = require_mention

    # LangSmith 可观测性（可选）
    console.print("\n[bold cyan]── 可观测性配置 (可选) ──[/bold cyan]")
    enable_langsmith = Confirm.ask(
        "是否启用 LangSmith 追踪？",
        default=False,
    )
    if enable_langsmith:
        config["agent"]["langsmith_enabled"] = True
        project = Prompt.ask("LangSmith Project", default="ccbot")
        config["agent"]["langsmith_project"] = project
        api_key = Prompt.ask("LangSmith API Key", password=True, default="")
        if api_key:
            config["agent"]["langsmith_api_key"] = api_key

    # 创建目录结构（workspace = config 所在目录）
    workspace_path = config_path.parent
    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / "memory").mkdir(parents=True, exist_ok=True)
    (workspace_path / "schedules").mkdir(parents=True, exist_ok=True)
    (workspace_path / "output").mkdir(parents=True, exist_ok=True)

    # 写入配置文件
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    # 完成提示
    console.print("\n[bold green]✓ 配置完成！[/bold green]")
    console.print(f"  配置文件: [cyan]{config_path}[/cyan]")
    console.print(f"  Workspace: [cyan]{workspace_path}[/cyan]")

    console.print("\n[bold]下一步:[/bold]")
    if mode == "1":
        console.print(f"  [cyan]uv run ccbot chat -c {config_path}[/cyan]")
    else:
        console.print(f"  [cyan]uv run ccbot run -c {config_path}[/cyan]")


@app.command()
def chat(
    message: Annotated[str | None, typer.Option("--message", "-m", help="单次消息")] = None,
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
    ws = WorkspaceManager(config_path.parent)
    langsmith_status = _augment_langsmith_metadata(config, entrypoint="chat", workspace=ws.path)
    logger.info("LangSmith: {}", _format_langsmith_status(langsmith_status))
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
    )
    agent = CCBotAgent(
        cfg,
        role=RuntimeRole.WORKER,
        extra_system_prompt=(
            "This is a single-run worker invocation.\n"
            f"When done, write a clear markdown summary of what you accomplished to: {out_path}"
        ),
    )

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
        return FeishuChannel(
            config.feishu,
            output_dir=workspace.output_dir,
            dedup_dir=workspace.dedup_dir,
            tmp_dir=workspace.tmp_dir,
        )
    if channel_type == "cli":
        from ccbot.channels.cli import CLIChannel

        return CLIChannel()
    raise typer.BadParameter(f"不支持的通道类型: {channel_type}")


@app.command()
def run(
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径（JSON）"),
    ] = _DEFAULT_CONFIG,
    channel_type: Annotated[
        str,
        typer.Option("--channel", help="通道类型（feishu|cli）"),
    ] = "feishu",
    web_port: Annotated[
        int,
        typer.Option("--web-port", help="嵌入 Web 控制台端口（0 = 关闭）"),
    ] = 8787,
    daemon: Annotated[
        bool,
        typer.Option("--daemon", "-d", help="后台运行（守护进程模式）"),
    ] = False,
    pid_file: Annotated[
        Path | None,
        typer.Option("--pid-file", help="PID 文件路径（后台模式有效）"),
    ] = None,
) -> None:
    """启动机器人（Supervisor+Worker 多 Agent 模式）。"""
    # 后台模式
    if daemon:
        actual_pid_file = pid_file or (config_path.parent / "ccbot.pid")
        console.print(f"[cyan]以后台模式启动，PID 文件: {actual_pid_file}[/cyan]")
        _daemonize(actual_pid_file)

    from ccbot.channels.base import IncomingMessage
    from ccbot.config import load_config
    from ccbot.scheduler import SchedulerService
    from ccbot.team import AgentTeam
    from ccbot.workspace import WorkspaceManager

    config = load_config(config_path)
    workspace = WorkspaceManager(config_path.parent)
    langsmith_status = _augment_langsmith_metadata(
        config,
        entrypoint="run",
        workspace=workspace.path,
        channel_type=channel_type,
    )
    logger.info("LangSmith: {}", _format_langsmith_status(langsmith_status))
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

    async def schedule_execute(job) -> str:
        return await team.ask(job.runtime_chat_id, job.prompt)

    async def schedule_notify(job, content: str) -> None:
        target = job.notify_target or team.last_chat_id
        if target:
            await channel.send(target, content)
        else:
            logger.warning("定时任务通知无目标，已跳过 job_id={}", job.job_id)

    scheduler = SchedulerService(
        workspace.path,
        schedule_execute,
        schedule_notify,
        poll_interval_s=config.agent.scheduler_poll_interval_s,
        job_timeout_s=config.agent.scheduler_job_timeout_s,
        config=config.agent,
    )
    team.set_scheduler(scheduler)

    web_info = ""
    if web_port > 0:
        web_info = f"\nWeb 控制台: [cyan]http://127.0.0.1:{web_port}[/cyan]"

    console.print(
        Panel(
            f"{__logo__} 启动 {channel_type} 机器人 (Supervisor+Worker)\n"
            f"Channel: [cyan]{channel_type}[/cyan]\n"
            f"Model:   [cyan]{config.agent.model or 'default'}[/cyan]\n"
            f"Workspace: [cyan]{workspace.path}[/cyan]\n"
            f"LangSmith: [cyan]{_format_langsmith_status(langsmith_status)}[/cyan]"
            f"{web_info}",
            border_style="cyan",
        )
    )

    async def main() -> None:
        await team.start()
        web_server: asyncio.Task[None] | None = None
        try:
            if config.agent.scheduler_enabled:
                await scheduler.start()

            # 嵌入 Web 控制台
            if web_port > 0:
                web_server = asyncio.create_task(
                    _run_embedded_web(
                        config_path, team, scheduler, web_port, live_config=config.agent
                    ),
                    name="embedded-web-console",
                )

            await channel.start()
            await channel.wait_closed()
        finally:
            if web_server is not None:
                web_server.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await web_server
            if config.agent.scheduler_enabled:
                await scheduler.stop()
            await channel.stop()
            await team.stop()

    asyncio.run(main())


@app.command()
def stop(
    pid_file: Annotated[
        Path,
        typer.Option("--pid-file", help="PID 文件路径"),
    ] = Path.home() / ".ccbot" / "ccbot.pid",
) -> None:
    """停止后台运行的 ccbot 进程。"""
    import signal

    if not pid_file.exists():
        console.print(f"[red]PID 文件不存在: {pid_file}[/red]")
        raise typer.Exit(1)

    try:
        pid = int(pid_file.read_text().strip())
    except ValueError:
        console.print(f"[red]PID 文件格式无效: {pid_file}[/red]")
        raise typer.Exit(1)

    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]已发送终止信号到进程 {pid}[/green]")
        pid_file.unlink(missing_ok=True)
    except ProcessLookupError:
        console.print(f"[yellow]进程 {pid} 不存在，清理 PID 文件[/yellow]")
        pid_file.unlink(missing_ok=True)
    except PermissionError:
        console.print(f"[red]无权限终止进程 {pid}[/red]")
        raise typer.Exit(1)


@app.command()
def web(
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径（JSON）"),
    ] = _DEFAULT_CONFIG,
    host: Annotated[str, typer.Option("--host", help="监听地址")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="监听端口")] = 8787,
) -> None:
    """启动本地 Web 控制台。"""
    import uvicorn

    from ccbot.webui import create_app

    app_instance = create_app(config_path)
    console.print(
        Panel(
            f"{__logo__} 启动 Web 控制台\n"
            f"Host: [cyan]{host}[/cyan]\n"
            f"Port: [cyan]{port}[/cyan]\n"
            f"Config: [cyan]{config_path}[/cyan]",
            border_style="cyan",
        )
    )
    uvicorn.run(app_instance, host=host, port=port, log_level="info")


async def _run_embedded_web(
    config_path: Path,
    team: AgentTeam,
    scheduler: SchedulerService,
    port: int,
    *,
    live_config: AgentConfig | None = None,
) -> None:
    """在 ccbot run 进程内以后台 task 运行 Web 控制台。"""
    import uvicorn

    from ccbot.webui import create_app

    web_app = create_app(config_path, team=team, scheduler=scheduler, live_config=live_config)
    server_config = uvicorn.Config(web_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(server_config)
    await server.serve()
