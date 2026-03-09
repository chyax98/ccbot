"""ccbot 配置模块。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_WORKSPACE = str(Path.home() / ".ccbot" / "workspace")
_DEFAULT_CONFIG = Path.home() / ".ccbot" / "config.json"


class AgentConfig(BaseModel):
    """Agent 配置。"""

    # 基础配置
    model: str = ""  # 模型名，空=SDK 默认; 如 "claude-opus-4-6"
    workspace: str = _DEFAULT_WORKSPACE
    max_turns: int = 10

    # SDK 配置
    # allowed_tools: 白名单覆盖（通常为空，工具权限由 .claude/settings.json 管理）
    allowed_tools: list[str] = Field(default_factory=list)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # 注入 claude 子进程的额外环境变量（如 ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL）
    env: dict[str, str] = Field(default_factory=dict)

    # LangSmith 可观测性配置（ClaudeSDKClient 官方原生 tracing）
    langsmith_enabled: bool = False
    langsmith_project: str = ""
    langsmith_name: str = "ccbot"
    langsmith_tags: list[str] = Field(default_factory=list)
    langsmith_metadata: dict[str, Any] = Field(default_factory=dict)
    langsmith_endpoint: str = ""
    langsmith_api_key: str = ""

    # Scheduler 配置
    scheduler_enabled: bool = True
    scheduler_poll_interval_s: int = 30

    # Heartbeat 配置
    heartbeat_enabled: bool = True
    heartbeat_interval: int = 1800  # 秒，默认 30 分钟
    heartbeat_notify_chat_id: str = ""  # 心跳结果通知目标，空则用最近活跃会话

    # Worker 模式配置（供 ccbot worker 命令使用）
    system_prompt: str = ""  # 直接指定 system prompt，非空时跳过 workspace 构建
    cwd: str = ""  # 工作目录覆盖，非空时替代 workspace.path

    # Supervisor 记忆配置
    supervisor_resume_enabled: bool = True  # 启动后优先基于持久化 session_id resume
    short_term_memory_turns: int = 12  # 本地短期记忆保存的最大轮数（user/assistant turn）

    # Session 配置
    idle_timeout: int = 28800  # Supervisor session 空闲超时秒数，默认 8 小时
    # 说明：每个 chat_id 对应一个 claude 子进程（~200-500 MB/个）。
    # disconnect 后子进程释放，但 Anthropic 服务端 session 独立保留；
    # runtime_session_id 持久化在磁盘，下次请求通过 resume 无缝续接。
    # 0 = 永不回收（不推荐：多群组场景会无限累积子进程）。
    worker_idle_timeout: int = 3600  # Worker session 空闲超时秒数，默认 1 小时
    # Worker 执行短期任务后通常可以更快回收，独立于 Supervisor 的 idle_timeout。

    # 多 Agent 编排配置
    max_workers: int = Field(default=4, ge=1, le=16)  # 最大并行 worker 数
    max_pooled_workers: int = Field(default=8, ge=1, le=64)  # Worker 池中最多保留的实例数


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

    # 进度消息配置
    progress_silent_s: int = 30  # 进度消息静默期（秒）
    progress_interval_s: int = 60  # 进度消息最小间隔（秒）

    # 消息配置
    msg_split_max_len: int = 3000  # 长消息分段最大字符数
    confirm_timeout_s: int = 300  # <<<CONFIRM>>> 等待超时（秒）

    # 消息处理超时
    msg_process_timeout_s: int = 600  # 消息处理超时（秒），默认 10 分钟

    # WebSocket 配置
    ws_reconnect_delay_s: int = 2  # WebSocket 初始重连延迟（秒）
    ws_reconnect_max_delay_s: int = 60  # WebSocket 最大重连延迟（秒）


class Config(BaseSettings):
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)

    model_config = SettingsConfigDict(
        env_prefix="CCBOT_",
        env_nested_delimiter="__",
    )


def load_config(path: Path = _DEFAULT_CONFIG) -> Config:
    """JSON 文件 > CCBOT_* 环境变量 > 默认值"""
    from pydantic_settings import JsonConfigSettingsSource

    class _Config(Config):
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings, env_settings, **_):  # type: ignore[override]
            json_source = (
                (JsonConfigSettingsSource(settings_cls, json_file=path),) if path.exists() else ()
            )
            return (init_settings, *json_source, env_settings)

    return _Config()
