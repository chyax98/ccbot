"""ccbot 配置模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

_DEFAULT_WORKSPACE = str(Path.home() / ".ccbot" / "workspace")
_DEFAULT_CONFIG = Path.home() / ".ccbot" / "config.json"


class AgentConfig(BaseModel):
    """Agent 配置。"""

    # 基础配置
    model: str = ""  # 模型名，空=SDK 默认; 如 "claude-opus-4-6"
    workspace: str = _DEFAULT_WORKSPACE
    max_turns: int = 10

    # SDK 配置
    allowed_tools: list[str] = Field(default_factory=list)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Heartbeat 配置
    heartbeat_enabled: bool = True
    heartbeat_interval: int = 1800  # 秒，默认 30 分钟
    heartbeat_notify_chat_id: str = ""  # 心跳结果通知目标，空则用最近活跃会话

    # Worker 模式配置（供 ccbot worker 命令使用）
    system_prompt: str = ""  # 直接指定 system prompt，非空时跳过 workspace 构建
    cwd: str = ""  # 工作目录覆盖，非空时替代 workspace.path

    # Session 配置
    idle_timeout: int = 28800  # 空闲超时秒数，默认 8 小时（28800 = 8*3600）
    # 说明：ClaudeSDKClient 的 session 保存在内存中，disconnect 后丢失
    # 设置较长的 idle_timeout 可保持 session 活跃，避免 memory 丢失
    # 0 表示永不自动关闭（不推荐，可能占用资源）

    # 多 Agent 编排配置
    max_workers: int = Field(default=4, ge=1, le=16)  # 最大并行 worker 数


class A2AConfig(BaseModel):
    """A2A 协议服务器配置（Agent-to-Agent 通信）。"""

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = 8765
    name: str = "ccbot"
    description: str = "Claude Agent SDK powered assistant with multi-agent orchestration"


class FeishuConfig(BaseModel):
    """飞书机器人配置。"""

    # 认证配置
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""

    # 权限配置
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    dm_policy: str = "open"  # 私聊策略: "open"=所有人 / "pairing"=仅 allow_from 白名单
    group_policy: str = "open"  # 群聊策略: "open"=所有群
    require_mention: bool = False  # 群聊是否需要 @bot 才响应

    # 交互配置
    react_emoji: str = "THINKING"  # 收到消息时的表情反应，飞书合法 emoji_type
    progress_mode: str = "milestone"  # "milestone"=每3条批量发送 / "verbose"=每条都发


class Config(BaseSettings):
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    a2a: A2AConfig = Field(default_factory=A2AConfig)

    model_config = SettingsConfigDict(
        env_prefix="CCBOT_",
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
        def settings_customise_sources(  # type: ignore[override]
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            **kwargs: Any,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings]
            if json_path.exists():
                sources.append(JsonConfigSettingsSource(settings_cls, json_file=json_path))
            return tuple(sources)

    return _Config()
