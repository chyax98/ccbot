"""FastAPI + Jinja2 web console for local ccbot management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ccbot import __version__
from ccbot.config import Config, load_config
from ccbot.models.schedule import ScheduledJob, ScheduleSpec
from ccbot.scheduler import SchedulerService
from ccbot.workspace import WorkspaceManager

_TEMPLATES = Path(__file__).parent / "templates"
_PROMPTS = Path(__file__).parents[1] / "templates" / "prompts"


def create_app(config_path: Path) -> FastAPI:
    """Create a local management console app."""

    app = FastAPI(title="ccbot web console", version=__version__)
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    state = _WebConsoleState(config_path)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        scheduler = state.load_scheduler(workspace.path)
        agent_files = state.list_agent_files(workspace.path)
        env_items = state.list_managed_env(runtime_config)
        config_exists = state.config_path.exists()
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            state.template_context(
                request,
                active="dashboard",
                runtime_config=runtime_config,
                workspace=workspace,
                scheduler_jobs=scheduler.list_jobs(),
                agent_files=agent_files,
                env_items=env_items,
                config_exists=config_exists,
            ),
        )

    @app.get("/scheduler", response_class=HTMLResponse)
    async def scheduler_page(request: Request) -> HTMLResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        scheduler = state.load_scheduler(workspace.path)
        return templates.TemplateResponse(
            request,
            "scheduler.html",
            state.template_context(
                request,
                active="scheduler",
                runtime_config=runtime_config,
                workspace=workspace,
                jobs=scheduler.list_jobs(),
                notice=request.query_params.get("notice", ""),
                error=request.query_params.get("error", ""),
            ),
        )

    @app.post("/scheduler/jobs")
    async def create_scheduler_job(request: Request) -> RedirectResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        scheduler = state.load_scheduler(workspace.path)
        form = await _read_form_body(request)
        try:
            spec = ScheduleSpec(
                name=form["name"],
                cron_expr=form["cron_expr"],
                timezone=form.get("timezone", "Asia/Shanghai"),
                prompt=form["prompt"],
                purpose=form.get("purpose", ""),
            )
        except Exception as exc:
            return _redirect_with_message("/scheduler", error=f"创建失败: {exc}")

        scheduler.create_job(
            spec,
            created_by="web-ui",
            channel="web",
            notify_target="",
            conversation_id="web-ui",
        )
        return _redirect_with_message("/scheduler", notice=f"已创建定时任务: {spec.name}")

    @app.post("/scheduler/{job_id}/toggle")
    async def toggle_scheduler_job(job_id: str, request: Request) -> RedirectResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        scheduler = state.load_scheduler(workspace.path)
        job = scheduler.get_job(job_id)
        if job is None:
            return _redirect_with_message("/scheduler", error=f"定时任务不存在: {job_id}")
        should_pause = job.enabled
        changed = scheduler.pause_job(job_id) if should_pause else scheduler.resume_job(job_id)
        if not changed:
            return _redirect_with_message("/scheduler", error=f"更新失败: {job_id}")
        action = "暂停" if should_pause else "恢复"
        return _redirect_with_message("/scheduler", notice=f"已{action}定时任务: {job.name}")

    @app.post("/scheduler/{job_id}/delete")
    async def delete_scheduler_job(job_id: str) -> RedirectResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        scheduler = state.load_scheduler(workspace.path)
        job = scheduler.get_job(job_id)
        if job is None:
            return _redirect_with_message("/scheduler", error=f"定时任务不存在: {job_id}")
        scheduler.delete_job(job_id)
        return _redirect_with_message("/scheduler", notice=f"已删除定时任务: {job.name}")

    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request) -> HTMLResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        return templates.TemplateResponse(
            request,
            "config.html",
            state.template_context(
                request,
                active="config",
                runtime_config=runtime_config,
                workspace=workspace,
                config_text=state.read_config_text(runtime_config),
                notice=request.query_params.get("notice", ""),
                error=request.query_params.get("error", ""),
            ),
        )

    @app.post("/config")
    async def save_config(request: Request) -> RedirectResponse:
        form = await _read_form_body(request)
        config_text = form.get("config_text", "").strip()
        if not config_text:
            return _redirect_with_message("/config", error="配置内容不能为空")
        try:
            payload = json.loads(config_text)
            Config.model_validate(payload)
        except Exception as exc:
            return _redirect_with_message("/config", error=f"配置校验失败: {exc}")
        state.write_config_payload(payload)
        return _redirect_with_message("/config", notice="配置文件已保存")

    @app.get("/agents", response_class=HTMLResponse)
    async def agents_page(request: Request) -> HTMLResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        return templates.TemplateResponse(
            request,
            "agents.html",
            state.template_context(
                request,
                active="agents",
                runtime_config=runtime_config,
                workspace=workspace,
                agent_files=state.list_agent_files(workspace.path),
                mcp_servers=runtime_config.agent.mcp_servers,
            ),
        )

    @app.get("/env", response_class=HTMLResponse)
    async def env_page(request: Request) -> HTMLResponse:
        runtime_config = state.load_runtime_config()
        workspace = state.load_workspace(runtime_config)
        return templates.TemplateResponse(
            request,
            "env.html",
            state.template_context(
                request,
                active="env",
                runtime_config=runtime_config,
                workspace=workspace,
                env_json=json.dumps(runtime_config.agent.env, ensure_ascii=False, indent=2),
                managed_env=state.list_managed_env(runtime_config),
                process_env=state.list_process_env(),
                notice=request.query_params.get("notice", ""),
                error=request.query_params.get("error", ""),
            ),
        )

    @app.post("/env")
    async def save_env(request: Request) -> RedirectResponse:
        form = await _read_form_body(request)
        env_text = form.get("env_json", "").strip() or "{}"
        try:
            payload = json.loads(env_text)
        except json.JSONDecodeError as exc:
            return _redirect_with_message("/env", error=f"环境变量 JSON 无法解析: {exc}")
        if not isinstance(payload, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in payload.items()
        ):
            return _redirect_with_message("/env", error="环境变量必须是 string -> string 的 JSON 对象")

        raw_config = state.read_config_payload()
        agent_config = raw_config.setdefault("agent", {})
        if not isinstance(agent_config, dict):
            return _redirect_with_message("/env", error="配置文件中的 agent 字段不是对象")
        agent_config["env"] = payload
        Config.model_validate(raw_config)
        state.write_config_payload(raw_config)
        return _redirect_with_message("/env", notice="agent.env 已保存")

    @app.get("/files")
    async def file_preview(request: Request, path: str) -> HTMLResponse:
        resolved = Path(path).expanduser().resolve()
        if not state.is_allowed_preview_path(resolved):
            raise HTTPException(status_code=404, detail="file not found")
        content = resolved.read_text(encoding="utf-8")
        return templates.TemplateResponse(
            request,
            "file_preview.html",
            state.template_context(
                request,
                active="agents",
                title=resolved.name,
                file_path=str(resolved),
                file_content=content,
            ),
        )

    return app


class _WebConsoleState:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path.expanduser().resolve()

    def load_runtime_config(self) -> Config:
        return load_config(self.config_path)

    def load_workspace(self, runtime_config: Config) -> WorkspaceManager:
        return WorkspaceManager(Path(runtime_config.agent.workspace))

    def load_scheduler(self, workspace_path: Path) -> SchedulerService:
        async def _noop_execute(job: ScheduledJob) -> str:
            return f"web-ui noop: {job.job_id}"

        async def _noop_notify(job: ScheduledJob, content: str) -> None:
            return None

        return SchedulerService(workspace_path, _noop_execute, _noop_notify)

    def template_context(self, request: Request, **extra: Any) -> dict[str, Any]:
        runtime_config = extra.get("runtime_config")
        workspace = extra.get("workspace")
        return {
            "request": request,
            "version": __version__,
            "config_path": str(self.config_path),
            "workspace_path": str(workspace.path) if workspace is not None else "",
            "model_name": runtime_config.agent.model if runtime_config is not None else "",
            **extra,
        }

    def read_config_payload(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("配置文件根节点必须是 JSON 对象")
        return raw

    def read_config_text(self, runtime_config: Config) -> str:
        if self.config_path.exists():
            return self.config_path.read_text(encoding="utf-8")
        return json.dumps(runtime_config.model_dump(mode="json"), ensure_ascii=False, indent=2)

    def write_config_payload(self, payload: dict[str, Any]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def list_agent_files(self, workspace_path: Path) -> list[dict[str, str]]:
        files: list[Path] = []
        files.extend(sorted(_PROMPTS.glob("*.md")))
        workspace_candidates = [
            workspace_path / ".claude" / "CLAUDE.md",
            workspace_path / ".claude" / "settings.json",
            workspace_path / "worker" / ".claude" / "CLAUDE.md",
            workspace_path / "worker" / ".claude" / "settings.json",
        ]
        files.extend(path for path in workspace_candidates if path.exists())
        files.extend(sorted((workspace_path / ".claude" / "skills").glob("*/SKILL.md")))

        items: list[dict[str, str]] = []
        for path in files:
            preview = ""
            if path.exists():
                lines = path.read_text(encoding="utf-8").splitlines()[:6]
                preview = "\n".join(lines)
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "kind": self._classify_agent_file(path, workspace_path),
                    "preview": preview,
                }
            )
        return items

    def list_managed_env(self, runtime_config: Config) -> list[dict[str, str]]:
        return [
            {"key": key, "value": _mask_secret(value)}
            for key, value in sorted(runtime_config.agent.env.items())
        ]

    def list_process_env(self) -> list[dict[str, str]]:
        interesting_prefixes = ("CCBOT_", "ANTHROPIC_", "LANGSMITH_")
        items = [
            {"key": key, "value": _mask_secret(value)}
            for key, value in sorted(os.environ.items())
            if key.startswith(interesting_prefixes)
        ]
        return items

    def is_allowed_preview_path(self, path: Path) -> bool:
        allowed_roots = [
            _PROMPTS.resolve(),
            self.config_path.parent.resolve(),
        ]
        runtime_config = self.load_runtime_config()
        workspace = self.load_workspace(runtime_config)
        allowed_roots.extend(
            [
                (workspace.path / ".claude").resolve(),
                (workspace.path / "worker" / ".claude").resolve(),
            ]
        )
        return any(root == path or root in path.parents for root in allowed_roots)

    @staticmethod
    def _classify_agent_file(path: Path, workspace_path: Path) -> str:
        if path.parent == _PROMPTS:
            return "prompt"
        if ".claude/skills" in str(path):
            return "skill"
        if workspace_path in path.parents or path == workspace_path:
            return "workspace"
        return "other"


async def _read_form_body(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    parsed = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in parsed.items()}


def _redirect_with_message(path: str, *, notice: str = "", error: str = "") -> RedirectResponse:
    query: list[str] = []
    if notice:
        query.append(f"notice={quote_plus(notice)}")
    if error:
        query.append(f"error={quote_plus(error)}")
    suffix = f"?{'&'.join(query)}" if query else ""
    return RedirectResponse(url=f"{path}{suffix}", status_code=303)


def _mask_secret(value: str) -> str:
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]}"
