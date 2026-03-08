"""A2A 服务器单元测试。"""

import pytest
from fastapi.testclient import TestClient

from ccbot.config import A2AConfig, AgentConfig
from ccbot.server import A2AServer
from ccbot.workspace import WorkspaceManager


@pytest.fixture
def mock_team(monkeypatch, tmp_path):
    """Mock AgentTeam，避免启动真实 Claude 子进程。"""
    workspace = WorkspaceManager(tmp_path / "workspace")

    class MockTeam:
        def __init__(self, config, workspace):
            self.last_chat_id = None

        async def ask(self, chat_id, prompt, on_progress=None):
            self.last_chat_id = chat_id
            if on_progress:
                await on_progress("🔧 Mock tool 1")
                await on_progress("🔧 Mock tool 2")
            return f"Mock reply for: {prompt[:50]}"

    return MockTeam(AgentConfig(), workspace)


@pytest.fixture
def a2a_server(mock_team):
    """创建 A2A 服务器实例。"""
    config = A2AConfig(
        enabled=True,
        host="localhost",
        port=8765,
        name="test-ccbot",
        description="Test agent",
    )
    return A2AServer(mock_team, config)


@pytest.fixture
def client(a2a_server):
    """FastAPI 测试客户端。"""
    return TestClient(a2a_server.app)


def test_agent_card(client):
    """测试 Agent Card 端点。"""
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200

    data = resp.json()
    assert data["name"] == "test-ccbot"
    assert data["description"] == "Test agent"
    assert "message/send" in data["capabilities"]
    assert "message/stream" in data["capabilities"]
    assert "http://localhost:8765/rpc" in data["endpoints"]["message/send"]


def test_message_send_success(client):
    """测试同步消息发送成功。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "contextId": "test-ctx-1",
            "message": "Hello, agent!",
        },
        "id": 1,
    }

    resp = client.post("/rpc", json=payload)
    assert resp.status_code == 200

    data = resp.json()
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert "result" in data
    assert data["result"]["contextId"] == "test-ctx-1"
    assert "Mock reply" in data["result"]["message"]


def test_message_send_missing_message(client):
    """测试缺少 message 参数。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "contextId": "test-ctx-2",
        },
        "id": 2,
    }

    resp = client.post("/rpc", json=payload)
    assert resp.status_code == 200

    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32602


def test_message_send_invalid_method(client):
    """测试无效的方法名。"""
    payload = {
        "jsonrpc": "2.0",
        "method": "invalid/method",
        "params": {},
        "id": 3,
    }

    resp = client.post("/rpc", json=payload)
    assert resp.status_code == 404

    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32601


def test_rpc_parse_error(client):
    """测试 JSON 解析错误。"""
    resp = client.post("/rpc", content="invalid json")
    assert resp.status_code == 400

    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == -32700
