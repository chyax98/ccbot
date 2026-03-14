from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from ccbot.scheduler import SchedulerService
from ccbot.webui import create_app
from ccbot.workspace import WorkspaceManager


def test_webui_dashboard_and_scheduler_management(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agent": {"workspace": str(workspace.path)}}, ensure_ascii=False),
        encoding="utf-8",
    )

    client = TestClient(create_app(config_path))

    response = client.get("/")
    assert response.status_code == 200
    assert "ccbot Control Center" in response.text
    assert str(workspace.path) in response.text

    create_response = client.post(
        "/scheduler/jobs",
        data={
            "name": "日报",
            "cron_expr": "0 9 * * *",
            "timezone": "Asia/Shanghai",
            "prompt": "生成日报",
            "purpose": "日报输出",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 303

    scheduler = SchedulerService(
        workspace.path,
        lambda job: None,  # type: ignore[arg-type]
        lambda job, content: None,  # type: ignore[arg-type]
    )
    jobs = scheduler.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].name == "日报"

    scheduler_page = client.get("/scheduler")
    assert scheduler_page.status_code == 200
    assert "日报" in scheduler_page.text


def test_webui_saves_config_and_env(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path / "workspace")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agent": {"workspace": str(workspace.path)}}, ensure_ascii=False),
        encoding="utf-8",
    )

    client = TestClient(create_app(config_path))

    config_response = client.post(
        "/config",
        data={
            "config_text": json.dumps(
                {
                    "agent": {
                        "workspace": str(workspace.path),
                        "scheduler_enabled": False,
                    }
                },
                ensure_ascii=False,
            )
        },
        follow_redirects=False,
    )
    assert config_response.status_code == 303
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved_config["agent"]["scheduler_enabled"] is False

    env_response = client.post(
        "/env",
        data={
            "env_json": json.dumps(
                {"ANTHROPIC_BASE_URL": "https://example.com"}, ensure_ascii=False
            )
        },
        follow_redirects=False,
    )
    assert env_response.status_code == 303

    env_saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert env_saved["agent"]["env"]["ANTHROPIC_BASE_URL"] == "https://example.com"

    agents_response = client.get("/agents")
    assert agents_response.status_code == 200
    assert "Prompt Stack" in agents_response.text
    assert "Skill Catalog" in agents_response.text


def test_webui_api_status_standalone(tmp_path: Path) -> None:
    """独立模式下 /api/status 返回 embedded=False。"""
    workspace = WorkspaceManager(tmp_path / "workspace")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agent": {"workspace": str(workspace.path)}}, ensure_ascii=False),
        encoding="utf-8",
    )
    client = TestClient(create_app(config_path))
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["embedded"] is False
    assert data["workers"] == []
    assert data["scheduler"]["enabled"] is False


def test_webui_runtime_api_requires_embedded(tmp_path: Path) -> None:
    """独立模式下运行时 API 返回 503。"""
    workspace = WorkspaceManager(tmp_path / "workspace")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agent": {"workspace": str(workspace.path)}}, ensure_ascii=False),
        encoding="utf-8",
    )
    client = TestClient(create_app(config_path))
    assert client.get("/api/workers").status_code == 503
    assert client.get("/api/scheduler/jobs").status_code == 503
    assert client.post("/api/workers/test/interrupt").status_code == 503
    assert client.post("/api/scheduler/abc/run").status_code == 503


def test_webui_dashboard_embedded_mode(tmp_path: Path) -> None:
    """嵌入模式下 dashboard 渲染 Runtime Status。"""
    workspace = WorkspaceManager(tmp_path / "workspace")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"agent": {"workspace": str(workspace.path)}}, ensure_ascii=False),
        encoding="utf-8",
    )

    async def _noop_execute(job):  # type: ignore[no-untyped-def]
        return ""

    async def _noop_notify(job, content):  # type: ignore[no-untyped-def]
        pass

    scheduler = SchedulerService(workspace.path, _noop_execute, _noop_notify)

    # 使用 mock team 测试嵌入模式
    from unittest.mock import MagicMock

    mock_team = MagicMock()
    mock_team.worker_pool.list_workers.return_value = []

    client = TestClient(create_app(config_path, team=mock_team, scheduler=scheduler))

    response = client.get("/")
    assert response.status_code == 200
    assert "embedded" in response.text
    assert "Runtime Status" in response.text

    # API 可用
    status = client.get("/api/status").json()
    assert status["embedded"] is True
    assert status["scheduler"]["enabled"] is True

    workers = client.get("/api/workers").json()
    assert workers == []
