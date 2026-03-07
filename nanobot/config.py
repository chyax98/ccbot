"""nanobot 配置模块。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_WORKSPACE = str(Path.home() / ".nanobot" / "workspace")


class AgentConfig(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int = 10
    mcp_servers: dict[str, dict] = Field(default_factory=dict)
    workspace: str = _DEFAULT_WORKSPACE
    heartbeat_interval: int = 1800  # 秒，默认 30 分钟
    heartbeat_enabled: bool = True


class FeishuConfig(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    react_emoji: str = "THUMBSUP"


class Config(BaseSettings):
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    model_config = SettingsConfigDict(
        env_prefix="NANOBOT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
    )
