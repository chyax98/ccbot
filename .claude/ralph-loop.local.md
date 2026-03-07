---
active: true
iteration: 1
session_id: 
max_iterations: 30
completion_promise: null
started_at: "2026-03-07T21:04:09Z"
---
# ccbot 架构重构 - Ralph 循环指令

## 项目目标

  将 ccbot 从单体 FeishuBot 重构为 OpenClaw 风格的分层架构，保持
  ClaudeAgent SDK 原生体验，重点优化 Agent 调度和可靠性。

记住：一定使用 claude agentsdk，充分挖掘claude agentsdk-python v0.1.48 版本的潜力，参考 openclaw 的整体方案，我们设计我们自己的方案，更强大 更简洁（claudecode 原生能力就很强），一定端到端完成任务才能结束，确保项目观测性强，和飞书的交互体验强大无比，调度系统强大，不要使用 pgsql等重型的，需要数据库就是要 sqlite 这样的，claude code 支持 skills subagent memory 工具 mcp 等能力，不要重复造轮子

## 核心架构

  Feishu Channel          # 通道适配
    ↓
  Inbound Pipeline        # 入站处理
    ├── Dedup (内存+JSON) # OpenClaw 式去重
    ├── Debounce (300ms)  # 防抖合并
    └── Queue (per-chat)  # 串行队列
    ↓
  Agent Runtime           # Agent 运行时
    ├── AgentPool         # Client 复用管理
    └── AgentTeam         # Supervisor-Worker 调度
    ↓
  Outbound                # 出站发送

## 技术约束

1. **个人使用**：单机，1-5 并发聊天，不重
2. **Agent SDK**：ClaudeSDKClient，每 chat 一实例
3. **去重**：内存 LRU + 异步 JSON 文件（OpenClaw 式）
4. **队列**：asyncio.Queue，同 chat 串行
5. **Dispatch**：结构化 Pydantic，替代文本解析

## 开发阶段与迭代

### Phase 0: 基线（第 1-2 轮）

  **目标**：命名统一 + src 布局 + 测试通过

  **任务清单**：

- [ ] 全局替换 `nanobot` → `ccbot`（代码、配置、路径）
- [ ] 确认 src 布局：`src/ccbot/` 为标准包
- [ ] 修复所有 `ruff` 错误
- [ ] 修复所有 `pytest` 失败
- [ ] 验证 `uv run python -m ccbot --help` 工作

  **检查点**：

```bash
  ruff check . && uv run pytest -xvs && echo "Phase 0 OK"

  提交信息：refactor: unify naming to ccbot

  ---
  Phase 1: Inbound Pipeline（第 3-6 轮）

  目标：实现 OpenClaw 式的入站处理

  Round 1: Dedup（内存+JSON）

  文件：src/ccbot/core/dedup.py

  要求：
  class DedupCache:
      """内存 LRU + 异步 JSON 持久化"""
      def __init__(self, ttl_ms: int = 86400000, max_size: int =
  1000):
          self._cache: OrderedDict[str, float] = OrderedDict()
          self._ttl_ms = ttl_ms
          self._max_size = max_size
          self._file_path: Path | None = None

      def check(self, key: str) -> bool:
          """检查是否已存在，不存在则记录"""

      async def persist(self) -> None:
          """异步刷盘到 JSON"""

  参考：OpenClaw extensions/feishu/src/dedup.ts

  测试：重启后去重仍然有效

  ---
  Round 2: Debounce

  文件：src/ccbot/core/debounce.py

  要求：
  - 300ms 固定延迟
  - 控制命令不防抖（/new, /stop, /help）
  - 文本消息合并（join with "\n"）

  ---
  Round 3: Per-chat Queue

  文件：src/ccbot/core/queue.py

  要求：
  class PerChatQueue:
      """每 chat 独立队列，串行处理"""
      def __init__(self):
          self._queues: dict[str, asyncio.Queue] = {}
          self._workers: dict[str, asyncio.Task] = {}

      async def enqueue(self, chat_id: str, handler: Callable[[],
  Awaitable[T]]) -> T:
          """将任务加入指定 chat 的队列，返回执行结果"""

  关键点：异常隔离，一个任务失败不影响队列继续

  ---
  Round 4: Pipeline 集成

  文件：src/ccbot/channels/feishu.py（新的适配器）

  要求：
  - 使用新的 Dedup + Debounce + Queue
  - 保持旧的 FeishuBot 作为兼容层（通过 feature flag）
  - 默认启用新 pipeline

  检查点：
  # 单元测试
  uv run pytest tests/core/ -xvs

  # 集成测试（手动）
  uv run python -m ccbot feishu --config ~/.ccbot/config.json
  # 发送消息，验证：
  # 1. 去重有效（重复 message_id 不处理）
  # 2. 防抖有效（快速发送 3 条合并为 1 次）
  # 3. 保序（消息按顺序处理）

  提交信息：feat: add inbound pipeline (dedup, debounce, queue)

  ---
  Phase 2: Agent Runtime（第 7-10 轮）

  Round 1: Dispatch Schema

  文件：src/ccbot/models/dispatch.py

  要求：
  class WorkerTask(BaseModel):
      name: str
      task: str  # 必填
      cwd: str = "."
      model: str = ""
      max_turns: int = 30

  class DispatchPayload(BaseModel):
      tasks: list[WorkerTask]

  替换：team.py 中的文本解析 <dispatch>...</dispatch> → 结构化

  ---
  Round 2: AgentPool

  文件：src/ccbot/runtime/pool.py

  要求：
  class AgentPool:
      """管理 ClaudeSDKClient 生命周期"""
      def __init__(self, config: AgentConfig, idle_timeout: int =
  1800):
          self._clients: dict[str, ClaudeSDKClient] = {}
          self._last_used: dict[str, float] = {}
          self._idle_timeout = idle_timeout

      async def acquire(self, chat_id: str) -> ClaudeSDKClient:
          """获取或创建 client"""

      async def release(self, chat_id: str) -> None:
          """标记使用，启动 idle 检查"""

      async def _cleanup_idle(self) -> None:
          """定期关闭空闲 client"""

  ---
  Round 3: AgentTeam 优化

  文件：src/ccbot/team.py（重构）

  要求：
  - 使用 DispatchPayload 替代 json.loads
  - Worker 异常隔离
  - 进度回调结构化

  ---
  Round 4: 集成测试

  检查点：
  # Worker dispatch 测试
  uv run pytest tests/test_team.py -xvs

  # 端到端测试
  # 1. 触发 multi-agent 任务
  # 2. 验证进度看板正常
  # 3. 验证 worker 并行执行

  提交信息：feat: optimize agent runtime with structured dispatch

  ---
  Phase 3: 通道抽象（第 11-14 轮）

  Round 1: Channel Base

  文件：src/ccbot/channels/base.py

  要求：
  class Channel(ABC):
      @abstractmethod
      async def start(self) -> None: ...

      @abstractmethod
      async def stop(self) -> None: ...

      @abstractmethod
      async def send(self, target: str, content: str) -> None: ...

  ---
  Round 2: Feishu 适配器

  文件：src/ccbot/channels/feishu/adapter.py

  要求：
  - 继承 Channel
  - 使用新的 Inbound Pipeline
  - 保持向后兼容

  ---
  Round 3: CLI 通道

  文件：src/ccbot/channels/cli.py

  要求：
  - 命令行交互模式
  - ccbot chat "message" 支持

  ---
  Round 4: 配置迁移

  文件：src/ccbot/config.py

  要求：
  - 添加 config_version: int = 2
  - 自动迁移 v1 → v2
  - 添加 feature flag：use_v2_pipeline: bool = True

  检查点：
  # 多通道测试
  uv run python -m ccbot cli --message "test"

  提交信息：feat: add channel abstraction and cli support

  ---
  Phase 4: 打磨与文档（第 15-16 轮）

  Round 1: 测试覆盖

  要求：
  - core/ 模块 > 80% 覆盖率
  - runtime/ 模块 > 70% 覆盖率
  - 集成测试：飞书端到端

  Round 2: 文档

  文件：
  - docs/ARCHITECTURE.md - 新架构说明
  - docs/MIGRATION.md - v1 → v2 迁移指南
  - README.md - 更新使用方式

  检查点：
  # 最终检查
  ruff check . && mypy src/ccbot && pytest --cov=src/ccbot
  --cov-report=term-missing

  提交信息：docs: add architecture docs and migration guide

  ---
  每轮开发规范

  1. 开始

  # 检查当前状态
  git status
  git pull --rebase

  2. 开发

  - 按任务清单实现
  - 先写测试，再写实现（TDD）
  - 保持文件 < 500 行

  3. 验证

  ruff check .
  ruff format .
  uv run pytest -xvs

  4. 提交

  git add <files>
  git commit -m "<type>: <description>"
  git push

  5. 报告

  本轮完成：
  - 实现内容
  - 测试结果
  - 下一轮计划

  ---
  风险处理

  ┌──────────┬──────────────────────────┐
  │   风险   │           应对           │
  ├──────────┼──────────────────────────┤
  │ 测试失败 │ 先修复，再提交；不要跳过 │
  ├──────────┼──────────────────────────┤
  │ 设计冲突 │ 暂停，报告用户确认       │
  ├──────────┼──────────────────────────┤
  │ 性能问题 │ 保留旧实现开关，A/B 对比 │
  ├──────────┼──────────────────────────┤
  │ 代码膨胀 │ 超过 500 行必须拆分      │
  └──────────┴──────────────────────────┘

  ---
  执行流程

  1. 读取当前代码状态
  2. 确定当前阶段
  3. 开始新的一轮开发
  4. 自动提交，review,端到端运行测试
```
