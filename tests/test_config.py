"""Tests for ccbot.config 配置模块。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ccbot.config import AgentConfig, Config, FeishuConfig, load_config


class TestAgentConfig:
    """AgentConfig 字段验证。"""

    def test_defaults(self) -> None:
        config = AgentConfig()
        assert config.model == ""
        assert config.max_turns == 10
        assert config.idle_timeout == 28800
        assert config.max_workers == 4
        assert config.allowed_tools == []
        assert config.mcp_servers == {}
        assert config.system_prompt == ""
        assert config.cwd == ""
        assert config.langsmith_enabled is False
        assert config.langsmith_project == ""
        assert config.langsmith_name == "ccbot"
        assert config.langsmith_tags == []
        assert config.langsmith_metadata == {}
        assert config.heartbeat_enabled is True
        assert config.heartbeat_interval == 1800

    def test_custom_values(self) -> None:
        config = AgentConfig(
            model="claude-opus-4-6",
            max_turns=50,
            idle_timeout=3600,
            max_workers=8,
        )
        assert config.model == "claude-opus-4-6"
        assert config.max_turns == 50
        assert config.idle_timeout == 3600
        assert config.max_workers == 8

    def test_max_workers_validation(self) -> None:
        """max_workers 必须在 [1, 16] 范围内。"""
        AgentConfig(max_workers=1)
        AgentConfig(max_workers=16)

        with pytest.raises(ValueError):
            AgentConfig(max_workers=0)
        with pytest.raises(ValueError):
            AgentConfig(max_workers=17)


class TestFeishuConfig:
    """FeishuConfig 字段验证。"""

    def test_defaults(self) -> None:
        config = FeishuConfig()
        assert config.app_id == ""
        assert config.app_secret == ""
        assert config.allow_from == ["*"]
        assert config.dm_policy == "open"
        assert config.require_mention is False

    def test_custom_allow_from(self) -> None:
        config = FeishuConfig(allow_from=["user1", "user2"])
        assert config.allow_from == ["user1", "user2"]


class TestConfig:
    """顶层 Config 测试。"""

    def test_nested_defaults(self) -> None:
        config = Config()
        assert config.agent.max_turns == 10
        assert config.feishu.app_id == ""


class TestLoadConfig:
    """load_config 函数测试。"""

    def test_load_from_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "agent": {
                        "model": "claude-opus-4-6",
                        "max_turns": 50,
                        "langsmith_enabled": True,
                        "langsmith_project": "ccbot-dev"
                    },
                    "feishu": {"app_id": "test_id"},
                },
                f,
            )
            f.flush()

            config = load_config(Path(f.name))

        assert config.agent.model == "claude-opus-4-6"
        assert config.agent.max_turns == 50
        assert config.agent.langsmith_enabled is True
        assert config.agent.langsmith_project == "ccbot-dev"
        assert config.feishu.app_id == "test_id"

    def test_load_nonexistent_uses_defaults(self) -> None:
        config = load_config(Path("/nonexistent/config.json"))
        assert config.agent.model == ""
        assert config.agent.max_turns == 10
