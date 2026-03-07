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
    agent = NanobotAgent(AgentConfig(), ws, extra_system_prompt=_SUPERVISOR_DISPATCH_PROMPT)

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


_SUPERVISOR_DISPATCH_PROMPT = """\
## Worker Agent Dispatch

当任务适合并行处理时，可以通过 Bash 工具自主启动 worker agent：

```bash
# 生成唯一 task_id，避免输出文件冲突
task_id=$(date +%s%N | cut -c1-12)

# 并行启动多个 worker（& 后台运行，wait 等待全部完成）
nanobot worker --cwd /path/to/frontend --output ~/.nanobot/tasks/$task_id/frontend.md "任务描述" &
nanobot worker --cwd /path/to/backend  --output ~/.nanobot/tasks/$task_id/backend.md  "任务描述" &
wait

# 用 Read 工具读取各 worker 的结果文件，综合后汇报给用户
```

可选参数：
- `--model claude-sonnet-4-6`：指定模型（默认继承）
- `--max-turns 30`：最大轮数（默认 30）

**冲突预防规则**：
- 每次调度生成新 task_id，输出目录自动隔离
- 同一 git 仓库内，Supervisor 应为各 worker 分配不重叠的文件/目录范围
- 不同仓库（不同 --cwd）的 worker 天然隔离，可自由并行
- Worker 之间不能直接通信，需要 Supervisor 居中传递信息
"""


@app.command()
def worker(
    task: str,
    cwd: Annotated[str, typer.Option("--cwd", "-C", help="工作目录（项目路径）")],
    output: Annotated[str, typer.Option("--output", "-o", help="结果写入路径（.md 文件）")],
    model: Annotated[str, typer.Option("--model", "-m", help="使用的模型")] = "",
    max_turns: Annotated[int, typer.Option("--max-turns", help="最大轮数")] = 30,
) -> None:
    """启动单次 worker agent 执行任务，结果写入文件（供 Supervisor 调用）。"""
    from nanobot.agent import NanobotAgent
    from nanobot.config import AgentConfig

    out_path = Path(output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cwd_resolved = str(Path(cwd).expanduser().resolve())

    system_prompt = (
        f"You are a focused AI coding assistant.\n"
        f"Working directory: {cwd_resolved}\n"
        f"Complete the assigned task thoroughly. When done, write a clear markdown "
        f"summary of what you accomplished to: {out_path}"
    )
    cfg = AgentConfig(
        model=model,
        max_turns=max_turns,
        cwd=cwd_resolved,
        system_prompt=system_prompt,
    )
    agent = NanobotAgent(cfg)

    async def run_worker() -> None:
        result = await agent.ask("worker", task)
        out_path.write_text(result, encoding="utf-8")
        logger.info("worker 完成，结果写入: {}", out_path)

    asyncio.run(run_worker())


@app.command()
def run(
    config_path: Annotated[
        Path,
        typer.Option("--config", "-c", help="配置文件路径（JSON）"),
    ] = _DEFAULT_CONFIG,
) -> None:
    """启动飞书机器人（使用 Claude Agent SDK 处理消息）。"""
    from nanobot.agent import NanobotAgent
    from nanobot.config import load_config
    from nanobot.feishu import FeishuBot
    from nanobot.heartbeat import HeartbeatService
    from nanobot.workspace import WorkspaceManager

    config = load_config(config_path)

    if not config.feishu.app_id or not config.feishu.app_secret:
        console.print("[red]错误: 飞书 App ID 和 App Secret 未配置[/red]")
        console.print(f"请在 {config_path} 中配置，或设置环境变量 NANOBOT_FEISHU__APP_ID / NANOBOT_FEISHU__APP_SECRET")
        raise typer.Exit(1)

    workspace = WorkspaceManager(Path(config.agent.workspace))
    agent = NanobotAgent(config.agent, workspace, extra_system_prompt=_SUPERVISOR_DISPATCH_PROMPT)

    async def on_message(text: str, chat_id: str, sender_id: str, send_progress) -> str:
        return await agent.ask(chat_id, text, on_progress=send_progress)

    bot = FeishuBot(config.feishu, on_message)

    async def heartbeat_execute(prompt: str) -> str:
        return await agent.ask("heartbeat", prompt)

    async def heartbeat_notify(content: str) -> None:
        target = config.agent.heartbeat_notify_chat_id or agent.last_chat_id
        if target:
            await bot.send(target, content)
        else:
            logger.warning("心跳通知无目标 chat_id，已跳过（可在配置中设置 heartbeat_notify_chat_id）")

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
