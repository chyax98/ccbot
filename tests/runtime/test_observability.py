"""Tests for LangSmith observability integration."""

from __future__ import annotations

import os
from types import SimpleNamespace

from ccbot import observability
from ccbot.config import AgentConfig


def _reset() -> None:
    observability._LANGSMITH_ATTEMPTED = False
    observability._LANGSMITH_CONFIGURED = False


def test_configure_langsmith_skips_when_disabled(monkeypatch) -> None:
    _reset()
    config = AgentConfig()
    called = {"imported": False}

    def fake_import(name: str):
        called["imported"] = True
        raise AssertionError(name)

    monkeypatch.setattr(observability.importlib, "import_module", fake_import)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING_V2", raising=False)

    assert observability.configure_langsmith_once(config) is False
    assert called["imported"] is False


def test_configure_langsmith_uses_official_integration(monkeypatch) -> None:
    _reset()
    config = AgentConfig(
        langsmith_enabled=True,
        langsmith_project="ccbot-prod",
        langsmith_name="ccbot-runtime",
        langsmith_tags=["prod", "feishu"],
        langsmith_metadata={"channel": "feishu"},
        langsmith_api_key="ls-api-key",
        langsmith_endpoint="https://api.smith.langchain.com",
    )
    seen = {}

    def fake_configure_claude_agent_sdk(**kwargs):
        seen.update(kwargs)
        return True

    fake_module = SimpleNamespace(configure_claude_agent_sdk=fake_configure_claude_agent_sdk)
    monkeypatch.setattr(observability.importlib, "import_module", lambda _: fake_module)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)

    assert observability.configure_langsmith_once(config) is True
    assert seen["name"] == "ccbot-runtime"
    assert seen["project_name"] == "ccbot-prod"
    assert seen["metadata"]["service"] == "ccbot"
    assert seen["metadata"]["channel"] == "feishu"
    assert seen["tags"] == ["ccbot", "prod", "feishu"]
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "ccbot-prod"
    assert os.environ["LANGSMITH_API_KEY"] == "ls-api-key"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://api.smith.langchain.com"


def test_configure_langsmith_warns_once_when_package_missing(monkeypatch) -> None:
    _reset()
    config = AgentConfig(langsmith_enabled=True)
    monkeypatch.setattr(
        observability.importlib,
        "import_module",
        lambda _: (_ for _ in ()).throw(ImportError("missing")),
    )

    assert observability.configure_langsmith_once(config) is False
    assert observability.configure_langsmith_once(config) is False
    assert observability._LANGSMITH_ATTEMPTED is True
    assert observability._LANGSMITH_CONFIGURED is False


def test_get_langsmith_status_reflects_env_and_metadata(monkeypatch) -> None:
    _reset()
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    config = AgentConfig(langsmith_enabled=True, langsmith_project="proj", model="sonnet")
    status = observability.get_langsmith_status(config)

    assert status["enabled"] is True
    assert status["project"] == "proj"
    assert status["name"] == "ccbot"
    assert status["api_key_set"] is False
    assert "configured_model" in status["metadata_keys"]


def test_build_metadata_includes_runtime_fields() -> None:
    config = AgentConfig(model="sonnet", max_turns=12, max_workers=3)
    metadata = observability._build_metadata(config)

    assert metadata["configured_model"] == "sonnet"
    assert metadata["max_turns"] == 12
    assert metadata["max_workers"] == 3
