"""Claude Agent SDK runtime role profiles.

将 preset、setting_sources、permission_mode 与 role prompt 维护集中到一处，
避免 Supervisor / Worker 的运行时配置散落在多个文件里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Any

from ccbot.config import AgentConfig

_PROMPT_SEPARATOR = "\n\n---\n\n"
_PROMPTS_DIR = Path(__file__).parent.parent / "templates" / "prompts"


class RuntimeRole(StrEnum):
    """Agent 运行时角色。"""

    SUPERVISOR = "supervisor"
    WORKER = "worker"
    REVIEWER = "reviewer"


@dataclass(frozen=True)
class RuntimeRoleProfile:
    """角色配置模板。"""

    role: RuntimeRole
    permission_mode: str
    setting_sources: tuple[str, ...] = ("project",)


_ROLE_PROFILES: dict[RuntimeRole, RuntimeRoleProfile] = {
    RuntimeRole.SUPERVISOR: RuntimeRoleProfile(
        role=RuntimeRole.SUPERVISOR,
        permission_mode="bypassPermissions",
    ),
    RuntimeRole.WORKER: RuntimeRoleProfile(
        role=RuntimeRole.WORKER,
        permission_mode="bypassPermissions",
    ),
    RuntimeRole.REVIEWER: RuntimeRoleProfile(
        role=RuntimeRole.REVIEWER,
        permission_mode="plan",
    ),
}


@cache
def load_role_prompt(role: RuntimeRole) -> str:
    """读取角色 prompt 模板。"""
    path = _PROMPTS_DIR / f"{role.value}.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def render_role_prompt(role: RuntimeRole, *, cwd: str | Path | None = None) -> str:
    """渲染角色 prompt。"""
    prompt = load_role_prompt(role)
    if not prompt:
        return ""
    if cwd is not None:
        prompt = prompt.replace("{{cwd}}", str(cwd))
    return prompt.strip()


def join_prompt_parts(*parts: str) -> str:
    """拼接多个 prompt 片段，自动跳过空内容。"""
    normalized = [part.strip() for part in parts if part and part.strip()]
    return _PROMPT_SEPARATOR.join(normalized)


def build_sdk_options(
    config: AgentConfig,
    *,
    role: RuntimeRole,
    cwd: str | Path,
    base_prompt: str = "",
    extra_prompt: str = "",
    model: str = "",
    max_turns: int | None = None,
    allowed_tools: list[str] | None = None,
    output_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 ClaudeAgentOptions 的 kwargs。"""
    profile = _ROLE_PROFILES[role]
    append_prompt = join_prompt_parts(
        base_prompt,
        render_role_prompt(role, cwd=cwd),
        extra_prompt,
    )

    system_prompt: dict[str, str] = {
        "type": "preset",
        "preset": "claude_code",
    }
    if append_prompt:
        system_prompt["append"] = append_prompt

    kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "cwd": str(cwd),
        "permission_mode": profile.permission_mode,
        "setting_sources": list(profile.setting_sources),
    }
    if model:
        kwargs["model"] = model
    if max_turns:
        kwargs["max_turns"] = max_turns
    if allowed_tools:
        kwargs["allowed_tools"] = allowed_tools
    if output_format is not None:
        kwargs["output_format"] = output_format
    if config.mcp_servers:
        kwargs["mcp_servers"] = config.mcp_servers
    if config.env:
        kwargs["settings"] = json.dumps({"env": config.env})
    return kwargs
