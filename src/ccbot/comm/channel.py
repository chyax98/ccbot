"""WorkerChannel：为每个 Worker 生成 MCP 通信配置和 system prompt 追加内容。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ccbot.comm.bus import InMemoryBus
    from ccbot.comm.context import InMemoryContext

_COMM_PROMPT_TEMPLATE = """\

## 协作通信

你是 Worker "{name}"，与 [{peers}] 并行工作。
通过 MCP 工具 ccbot-comm 可以与其他 Worker 和 Supervisor 通信：

- **ccbot_send_message**: 发消息（to="supervisor" 上报，to="worker名" 点对点，to="" 广播）
- **ccbot_read_messages**: 读取收到的消息
- **ccbot_list_workers**: 查看协作伙伴列表
- **ccbot_set_shared / ccbot_get_shared**: 共享状态读写（所有 Worker 可见）
- **ccbot_list_shared**: 列出所有共享状态键名
- **ccbot_report_progress**: 向 Supervisor 汇报进度

建议用法：
- 完成关键步骤后用 ccbot_set_shared 保存结果
- 需要同伴结果时用 ccbot_get_shared 读取
- 遇到重要进展或问题时用 ccbot_report_progress 汇报
"""


class WorkerChannel:
    """为单个 Worker 生成通信配置。

    使用 SDK 进程内 MCP 服务器，Worker 身份通过闭包捕获，
    工具参数无需传入 worker_name。
    """

    def __init__(
        self,
        bus: InMemoryBus,
        context: InMemoryContext,
        session_id: str,
        worker_name: str,
        peer_names: list[str],
    ) -> None:
        self._bus = bus
        self._context = context
        self._session_id = session_id
        self._worker_name = worker_name
        self._peer_names = peer_names

    @property
    def mcp_servers(self) -> dict[str, Any]:
        """生成 mcp_servers 配置，注入到 Worker 的 AgentConfig。"""
        from ccbot.comm.server import create_worker_mcp_server

        return {
            "ccbot-comm": create_worker_mcp_server(
                self._bus, self._context, self._session_id, self._worker_name
            )
        }

    @property
    def system_prompt_addition(self) -> str:
        """生成通信工具使用说明，追加到 Worker system prompt。"""
        peers = ", ".join(self._peer_names)
        return _COMM_PROMPT_TEMPLATE.format(name=self._worker_name, peers=peers)
