"""nanobot 配置模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Tuple, Type

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_DEFAULT_WORKSPACE = str(Path.home() / ".nanobot" / "workspace")
_DEFAULT_CONFIG = Path.home() / ".nanobot" / "config.json"


class AgentConfig(BaseModel):
    allowed_tools: list[str] = Field(default_factory=list)
    max_turns: int = 10
    mcp_servers: dict[str, dict] = Field(default_factory=dict)
    workspace: str = _DEFAULT_WORKSPACE
    heartbeat_interval: int = 1800  # 秒，默认 30 分钟
    heartbeat_enabled: bool = True
    heartbeat_notify_chat_id: str = ""  # 心跳结果通知目标，空则用最近活跃会话


class FeishuConfig(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    react_emoji: str = "THUMBSUP"
    # 私聊策略: "open"=所有人 / "pairing"=仅 allow_from 白名单
    dm_policy: str = "open"
    # 群聊策略: "open"=所有群（目前唯一选项，预留扩展）
    group_policy: str = "open"
    # 群聊是否需要 @bot 才响应
    require_mention: bool = False


class Config(BaseSettings):
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    model_config = SettingsConfigDict(
        env_prefix="NANOBOT_",
        env_nested_delimiter="__",
    )


def load_config(path: Path = _DEFAULT_CONFIG) -> Config:
    """加载配置：JSON 文件为基础，环境变量优先级更高。

    优先级：环境变量 > JSON 文件 > 默认值
    """
    from pydantic_settings import JsonConfigSettingsSource

    json_path = path

    class _Config(Config):
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: Type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            **kwargs: Any,
        ) -> Tuple[PydanticBaseSettingsSource, ...]:
            sources: list[Any] = [init_settings, env_settings]
            if json_path.exists():
                sources.append(JsonConfigSettingsSource(settings_cls, json_file=json_path))
            return tuple(sources)

    return _Config()
