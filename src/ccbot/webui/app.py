"""FastAPI + Jinja2 web console for local ccbot management."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote_plus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

from ccbot import __version__
from ccbot.config import AgentConfig, Config, load_config
from ccbot.models.schedule import ScheduledJob, ScheduleSpec
from ccbot.scheduler import SchedulerService
from ccbot.workspace import WorkspaceManager

if TYPE_CHECKING:
    from ccbot.team import AgentTeam

_TEMPLATES = Path(__file__).parent / "templates"
_PROMPTS = Path(__file__).parents[1] / "templates" / "prompts"


def create_app(
    config_path: Path,
    *,
    team: AgentTeam | None = None,
    scheduler: SchedulerService | None = None,
    live_config: AgentConfig | None = None,
) -> FastAPI:
    """Create a local management console app.

    当 team / scheduler 非 None 时，进入"嵌入模式"——
    提供运行时只读监控和控制 API。
    """

    app = FastAPI(title="ccbot web console", version=__version__)
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    state = _WebConsoleState(config_path, team=team, scheduler=scheduler, live_config=live_config)

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
                embedded=state.embedded,
                live_workers=state.snapshot_workers(),
                live_scheduler=state.snapshot_scheduler(),
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

        job = scheduler.create_job(
            spec,
            created_by="web-ui",
            channel="web",
            notify_target="",
            conversation_id="web-ui",
        )
        logger.info("[WebUI] 创建定时任务: {} ({})", spec.name, job.job_id)
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
        logger.info("[WebUI] {}定时任务: {} ({})", action, job.name, job_id)
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
        logger.info("[WebUI] 删除定时任务: {} ({})", job.name, job_id)
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
        logger.info("[WebUI] 保存配置文件")
        changed = state.reload_runtime_config(payload)
        if changed:
            notice = f"配置已保存并热重载: {', '.join(changed)}"
        else:
            notice = "配置文件已保存"
        return _redirect_with_message("/config", notice=notice)

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
                agent_surface=state.build_agent_surface(workspace.path, runtime_config),
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
            return _redirect_with_message(
                "/env", error="环境变量必须是 string -> string 的 JSON 对象"
            )

        raw_config = state.read_config_payload()
        agent_config = raw_config.setdefault("agent", {})
        if not isinstance(agent_config, dict):
            return _redirect_with_message("/env", error="配置文件中的 agent 字段不是对象")
        agent_config["env"] = payload
        Config.model_validate(raw_config)
        state.write_config_payload(raw_config)
        logger.info("[WebUI] 保存 agent.env ({} 个变量)", len(payload))
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

    # ── Runtime API（仅嵌入模式可用） ──

    def _require_embedded() -> None:
        if not state.embedded:
            raise HTTPException(status_code=503, detail="运行时 API 仅在嵌入模式下可用")

    @app.get("/api/status")
    async def api_status() -> JSONResponse:
        """运行时总览。"""
        return JSONResponse(
            {
                "embedded": state.embedded,
                "version": __version__,
                "workers": state.snapshot_workers(),
                "scheduler": state.snapshot_scheduler(),
            }
        )

    @app.get("/api/workers")
    async def api_workers() -> JSONResponse:
        _require_embedded()
        return JSONResponse(state.snapshot_workers())

    @app.post("/api/workers/{name}/interrupt")
    async def api_worker_interrupt(name: str) -> JSONResponse:
        _require_embedded()
        assert state._team is not None
        ok = await state._team.worker_pool.interrupt(name)
        return JSONResponse({"ok": ok, "name": name, "action": "interrupt"})

    @app.post("/api/workers/{name}/kill")
    async def api_worker_kill(name: str) -> JSONResponse:
        _require_embedded()
        assert state._team is not None
        await state._team.worker_pool.kill(name)
        return JSONResponse({"ok": True, "name": name, "action": "kill"})

    @app.get("/api/scheduler/jobs")
    async def api_scheduler_jobs() -> JSONResponse:
        _require_embedded()
        return JSONResponse(state.snapshot_scheduler())

    @app.post("/api/scheduler/{job_id}/run")
    async def api_scheduler_run(job_id: str) -> JSONResponse:
        _require_embedded()
        if state._live_scheduler is None:
            raise HTTPException(status_code=404, detail="Scheduler 未启用")
        result = await state._live_scheduler.run_job_now(job_id)
        return JSONResponse({"ok": result != "missing", "job_id": job_id, "result": result})

    return app


_HOT_RELOADABLE_FIELDS: frozenset[str] = frozenset(
    {
        "scheduler_poll_interval_s",
        "scheduler_job_timeout_s",
        "worker_idle_timeout",
        "max_pooled_workers",
        "idle_timeout",
        "max_workers",
        "max_turns",
        "model",
        "short_term_memory_turns",
    }
)


class _WebConsoleState:
    def __init__(
        self,
        config_path: Path,
        *,
        team: AgentTeam | None = None,
        scheduler: SchedulerService | None = None,
        live_config: AgentConfig | None = None,
    ) -> None:
        self.config_path = config_path.expanduser().resolve()
        self._team = team
        self._live_scheduler = scheduler
        self._live_config = live_config

    @property
    def embedded(self) -> bool:
        """是否处于嵌入模式（在 ccbot run 进程内运行）。"""
        return self._team is not None

    def snapshot_workers(self) -> list[dict[str, Any]]:
        """返回当前 WorkerPool 的快照（嵌入模式）。"""
        if self._team is None:
            return []
        return [
            {
                "name": info.name,
                "status": info.status.value,
                "cwd": info.cwd,
                "model": info.model,
                "owner_id": info.owner_id,
                "task_count": info.task_count,
                "last_used": info.last_used,
                "idle_seconds": round(time.time() - info.last_used),
            }
            for info in self._team.worker_pool.list_workers()
        ]

    def snapshot_scheduler(self) -> dict[str, Any]:
        """返回当前 Scheduler 的快照（嵌入模式）。"""
        if self._live_scheduler is None:
            return {"enabled": False, "jobs": [], "active_runs": []}
        jobs = self._live_scheduler.list_jobs()
        return {
            "enabled": True,
            "jobs": [job.model_dump() for job in jobs],
            "active_runs": sorted(self._live_scheduler.active_runs),
        }

    def reload_runtime_config(self, new_payload: dict[str, Any]) -> list[str]:
        """将新配置中可热重载的字段写入运行时 AgentConfig 对象。返回变更字段列表。"""
        if self._live_config is None:
            return []
        new_agent = new_payload.get("agent", {})
        if not isinstance(new_agent, dict):
            return []
        changed: list[str] = []
        for field_name in _HOT_RELOADABLE_FIELDS:
            if field_name not in new_agent:
                continue
            new_value = new_agent[field_name]
            old_value = getattr(self._live_config, field_name, None)
            if new_value != old_value:
                setattr(self._live_config, field_name, new_value)
                changed.append(field_name)
        if changed:
            logger.info("Config 热重载生效: {}", ", ".join(changed))
        return changed

    def load_runtime_config(self) -> Config:
        return load_config(self.config_path)

    def load_workspace(self, runtime_config: Config) -> WorkspaceManager:
        return WorkspaceManager(self.config_path.parent)

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

    def build_agent_surface(self, workspace_path: Path, runtime_config: Config) -> dict[str, Any]:
        supervisor_claude = workspace_path / ".claude" / "CLAUDE.md"
        supervisor_settings = workspace_path / ".claude" / "settings.json"
        worker_claude = workspace_path / "worker" / ".claude" / "CLAUDE.md"
        worker_settings = workspace_path / "worker" / ".claude" / "settings.json"

        prompt_layers = [
            {
                "lane": "Supervisor",
                "title": "Runtime Context",
                "kind": "injected",
                "path": "",
                "summary": "ccbot 每轮注入 current date、worker 状态、schedule 状态。",
            },
            {
                "lane": "Supervisor",
                "title": "Supervisor Prompt",
                "kind": "prompt",
                "path": str(_PROMPTS / "supervisor.md"),
                "summary": "负责意图判断、结构化 dispatch、schedule create/manage。",
            },
            {
                "lane": "Supervisor",
                "title": "Workspace CLAUDE.md",
                "kind": "workspace",
                "path": str(supervisor_claude),
                "summary": "主 workspace 的项目级约束和技能加载入口。",
            },
            {
                "lane": "Supervisor",
                "title": "Workspace Settings",
                "kind": "policy",
                "path": str(supervisor_settings),
                "summary": "主会话工具边界；例如禁用原生 Agent / SendMessage。",
            },
            {
                "lane": "Worker",
                "title": "Worker Prompt",
                "kind": "prompt",
                "path": str(_PROMPTS / "worker.md"),
                "summary": "面向专项执行，强调完成子任务并返回结果。",
            },
            {
                "lane": "Worker",
                "title": "Worker CLAUDE.md",
                "kind": "workspace",
                "path": str(worker_claude),
                "summary": "Worker cwd 下的最小模板；避免继承 Supervisor 的全部上下文。",
            },
            {
                "lane": "Worker",
                "title": "Worker Settings",
                "kind": "policy",
                "path": str(worker_settings),
                "summary": "Worker 侧工具权限，与主 workspace 保持边界一致。",
            },
        ]

        skills = self._load_skills(workspace_path)
        mcp_servers = self._summarize_mcp_servers(runtime_config.agent.mcp_servers)
        tool_policies = self._load_tool_policies([supervisor_settings, worker_settings])

        roles = [
            {
                "name": "Supervisor",
                "chip": "Orchestrator",
                "summary": "负责理解用户意图、维护本地记忆、决定 respond / dispatch / scheduler 动作。",
                "points": [
                    "持有长期/短期记忆与 runtime session。",
                    "可创建和管理 Scheduler job。",
                    "遇到可并行任务时，结构化派发给 WorkerPool。",
                ],
            },
            {
                "name": "Worker",
                "chip": "Executor",
                "summary": "负责隔离执行子任务，不保存 Supervisor 级记忆，生命周期由 WorkerPool 控制。",
                "points": [
                    "按 task.cwd 启动，适合 repo review、专项改动、长时间执行。",
                    "同 owner_id 下可复用、可中断、可销毁。",
                    "只返回结果，不负责最终面向用户的综合表达。",
                ],
            },
            {
                "name": "Skills + MCP",
                "chip": "Capability Surface",
                "summary": "Skills 负责提示词级流程扩展，MCP 负责外部工具或服务接入，两者共同扩展 Agent 能力。",
                "points": [
                    "Skills 是 workspace 内声明式能力包，偏工作流与知识约束。",
                    "MCP server 是运行时接入点，偏工具和外部系统桥接。",
                    "两者都属于能力面，不直接替代 runtime 的 worker/scheduler 控制。",
                ],
            },
        ]

        architecture_steps = [
            {
                "title": "Intent Intake",
                "detail": "Supervisor 读取用户输入，再叠加 runtime_context、memory 和 prompt 约束。",
            },
            {
                "title": "Decision Layer",
                "detail": "输出 respond / dispatch / schedule_create / schedule_manage。",
            },
            {
                "title": "Capability Layer",
                "detail": "Skills 提供工作流语义，MCP 提供外部连接面，settings 锁定工具边界。",
            },
            {
                "title": "Execution Layer",
                "detail": "WorkerPool 执行子任务，Scheduler 负责持久化周期任务。",
            },
        ]

        control_planes = [
            {
                "name": "Prompt",
                "purpose": "定义角色边界、结构化协议、回答风格。",
                "storage": "src/ccbot/templates/prompts/*.md",
                "effect": "直接影响 Supervisor / Worker 的推理与输出格式。",
            },
            {
                "name": "Skills",
                "purpose": "沉淀可复用工作流、领域知识和工具使用约束。",
                "storage": f"{workspace_path}/.claude/skills/*/SKILL.md",
                "effect": "扩展 Agent 的方法论和任务套路。",
            },
            {
                "name": "MCP",
                "purpose": "接入文档、浏览器、内部系统、数据库等外部能力。",
                "storage": "config.agent.mcp_servers",
                "effect": "扩展 Agent 可访问的工具与服务面。",
            },
            {
                "name": "Config",
                "purpose": "控制 model、workspace、worker 数量、scheduler 开关。",
                "storage": str(self.config_path),
                "effect": "决定整个 runtime 的运行参数和外部注入。",
            },
            {
                "name": "Scheduler",
                "purpose": "把周期性动作落成持久化任务，而不是会话内临时 loop。",
                "storage": f"{workspace_path}/schedules/jobs.json",
                "effect": "负责长期自动化执行，不属于单次 prompt。",
            },
        ]

        stats = {
            "skills_count": len(skills),
            "always_on_skills": sum(1 for item in skills if item["always"]),
            "mcp_count": len(mcp_servers),
            "policy_block_count": sum(len(item["disallowed_tools"]) for item in tool_policies),
        }

        return {
            "architecture_steps": architecture_steps,
            "control_planes": control_planes,
            "roles": roles,
            "prompt_layers": prompt_layers,
            "skills": skills,
            "always_skills": [item for item in skills if item["always"]],
            "catalog_skills": [item for item in skills if not item["always"]],
            "mcp_servers": mcp_servers,
            "tool_policies": tool_policies,
            "stats": stats,
        }

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

    def _load_skills(self, workspace_path: Path) -> list[dict[str, Any]]:
        skills_root = workspace_path / ".claude" / "skills"
        items: list[dict[str, Any]] = []
        for path in sorted(skills_root.glob("*/SKILL.md")):
            content = path.read_text(encoding="utf-8")
            frontmatter, body = _split_frontmatter(content)
            metadata = frontmatter.get("metadata", {})
            ccbot_meta = metadata.get("ccbot", {}) if isinstance(metadata, dict) else {}
            requirements = ccbot_meta.get("requires", {}) if isinstance(ccbot_meta, dict) else {}
            preview_lines = [line for line in body.splitlines() if line.strip()][:4]
            items.append(
                {
                    "name": frontmatter.get("name") or path.parent.name,
                    "description": frontmatter.get("description") or "No description",
                    "emoji": ccbot_meta.get("emoji", "•") if isinstance(ccbot_meta, dict) else "•",
                    "always": bool(frontmatter.get("always", False)),
                    "bins": requirements.get("bins", []) if isinstance(requirements, dict) else [],
                    "path": str(path),
                    "preview": " ".join(preview_lines[:2]),
                }
            )
        return items

    def _summarize_mcp_servers(self, servers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name, payload in sorted(servers.items()):
            transport = "custom"
            endpoint = ""
            if "command" in payload:
                transport = "stdio"
                endpoint = str(payload.get("command", ""))
            elif "url" in payload:
                transport = "http"
                endpoint = str(payload.get("url", ""))
            elif "transport" in payload:
                transport = str(payload.get("transport"))
            env_keys = []
            env_payload = payload.get("env", {})
            if isinstance(env_payload, dict):
                env_keys = sorted(str(key) for key in env_payload)
            items.append(
                {
                    "name": name,
                    "transport": transport,
                    "endpoint": endpoint,
                    "env_keys": env_keys,
                    "keys": sorted(payload.keys()),
                    "raw": json.dumps(payload, ensure_ascii=False, indent=2),
                }
            )
        return items

    def _load_tool_policies(self, paths: list[Path]) -> list[dict[str, Any]]:
        policies: list[dict[str, Any]] = []
        for path in paths:
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            disallowed_tools = payload.get("disallowedTools", [])
            policies.append(
                {
                    "name": path.parent.parent.name if path.parent.parent.name else "workspace",
                    "path": str(path),
                    "disallowed_tools": disallowed_tools
                    if isinstance(disallowed_tools, list)
                    else [],
                    "raw": json.dumps(payload, ensure_ascii=False, indent=2),
                }
            )
        return policies


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


def _split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content

    parts = content.split("\n---\n", 1)
    if len(parts) != 2:
        return {}, content

    raw_frontmatter = parts[0].splitlines()[1:]
    body = parts[1]
    parsed: dict[str, Any] = {}
    for line in raw_frontmatter:
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        value = raw_value.strip()
        if not value:
            parsed[key.strip()] = ""
            continue
        if value in {"true", "false"}:
            parsed[key.strip()] = value == "true"
            continue
        if value.startswith('"') and value.endswith('"'):
            parsed[key.strip()] = value[1:-1]
            continue
        if value.startswith("{") or value.startswith("["):
            try:
                parsed[key.strip()] = json.loads(value)
                continue
            except json.JSONDecodeError:
                pass
        parsed[key.strip()] = value
    return parsed, body
