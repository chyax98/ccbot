# Nanobot 飞书架构改进实施检查清单

> [归档说明]
> 本文档已转为历史参考。执行优先级与阶段顺序请以 [ARCHITECTURE_FINAL_PLANS.md](ARCHITECTURE_FINAL_PLANS.md) 为准。

本文档提供详细的实施步骤和进度跟踪。

---

## Phase 1: 基础设施 (预计 1-2 天)

### 1.1 环境准备

- [ ] 安装依赖
  ```bash
  uv pip install aiosqlite pytest-asyncio
  ```

- [ ] 创建目录结构
  ```bash
  mkdir -p nanobot/channels/feishu
  mkdir -p tests/channels/feishu
  mkdir -p docs
  ```

- [ ] 创建 `nanobot/channels/__init__.py`
  ```python
  """消息通道模块"""
  
  from nanobot.channels.feishu import FeishuBot
  
  __all__ = ["FeishuBot"]
  ```

### 1.2 实现去重模块

- [ ] 创建 `nanobot/channels/feishu/dedup.py`
  - [ ] 实现 `DeduplicationStore` 协议
  - [ ] 实现 `SQLiteDeduplicationStore`
  - [ ] 实现 `MemoryDeduplicationStore`（测试用）
  - [ ] 添加数据库初始化逻辑
  - [ ] 添加过期清理功能

- [ ] 创建 `tests/channels/feishu/test_dedup.py`
  - [ ] 测试内存缓存命中
  - [ ] 测试数据库持久化
  - [ ] 测试并发安全性
  - [ ] 测试过期清理

### 1.3 实现队列模块

- [ ] 创建 `nanobot/channels/feishu/queue.py`
  - [ ] 实现 `PerChatMessageQueue`
  - [ ] 实现队列工作器生命周期管理
  - [ ] 实现优雅关闭
  - [ ] 添加上限保护（max_concurrent_chats）

- [ ] 创建 `tests/channels/feishu/test_queue.py`
  - [ ] 测试消息顺序保证
  - [ ] 测试并发处理
  - [ ] 测试队列关闭
  - [ ] 测试错误处理

---

## Phase 2: 核心功能 (预计 2-3 天)

### 2.1 会话管理

- [ ] 创建 `nanobot/channels/feishu/session.py`
  - [ ] 定义 `SessionScope` Enum
  - [ ] 实现 `SessionKey` dataclass
  - [ ] 实现 `FeishuSessionResolver`
  - [ ] 实现 `SessionManager`

- [ ] 创建 `tests/channels/feishu/test_session.py`
  - [ ] 测试各种 scope 的 key 生成
  - [ ] 测试话题 ID 稳定性
  - [ ] 测试 parent_key 关联

### 2.2 消息防抖

- [ ] 创建 `nanobot/channels/feishu/debounce.py`
  - [ ] 实现 `MessageDebouncer`
  - [ ] 实现 `extract_feishu_debounce_key`
  - [ ] 实现 `should_debounce_feishu_message`
  - [ ] 添加最大等待时间保护

- [ ] 创建 `tests/channels/feishu/test_debounce.py`
  - [ ] 测试消息合并
  - [ ] 测试防抖触发
  - [ ] 测试控制命令不防抖
  - [ ] 测试关闭刷新

### 2.3 权限系统

- [ ] 创建 `nanobot/channels/feishu/policy.py`
  - [ ] 实现 `DMPolicy`, `GroupPolicy` Enum
  - [ ] 实现 `PairingManager`
  - [ ] 实现 `PolicyChecker`
  - [ ] 添加配对码生成和验证
  - [ ] 添加磁盘持久化

- [ ] 创建 `tests/channels/feishu/test_policy.py`
  - [ ] 测试配对流程
  - [ ] 测试各种策略组合
  - [ ] 测试白名单匹配
  - [ ] 测试群组特定配置

---

## Phase 3: 整合 (预计 2 天)

### 3.1 SDK 客户端封装

- [ ] 创建 `nanobot/channels/feishu/client.py`
  - [ ] 封装 `lark.Client` 初始化
  - [ ] 实现 WebSocket 连接管理
  - [ ] 实现重连机制（指数退避）
  - [ ] 添加事件分发
  - [ ] 获取 bot_open_id

- [ ] 创建 `nanobot/channels/feishu/parser.py`
  - [ ] 提取消息解析逻辑
  - [ ] 处理各种 msg_type
  - [ ] 提取 @mention 信息

### 3.2 消息发送

- [ ] 创建 `nanobot/channels/feishu/send.py`
  - [ ] 封装发送文本消息
  - [ ] 封装发送卡片消息
  - [ ] 实现 PATCH 更新卡片
  - [ ] 处理回复消息（reply_to_message_id）

### 3.3 媒体处理

- [ ] 创建 `nanobot/channels/feishu/media.py`
  - [ ] 实现图片上传/下载
  - [ ] 实现文件上传/下载
  - [ ] 处理富文本中的媒体

### 3.4 卡片构建

- [ ] 创建 `nanobot/channels/feishu/card.py`
  - [ ] 提取现有卡片构建逻辑
  - [ ] 支持 Markdown 表格转换
  - [ ] 支持代码块高亮

---

## Phase 4: 新 Bot 实现 (预计 2 天)

### 4.1 主 Bot 类

- [ ] 创建 `nanobot/channels/feishu/bot.py`
  - [ ] 实现新的 `FeishuBot` 类
  - [ ] 整合所有子模块
  - [ ] 保持向后兼容的 API
  - [ ] 添加详细日志

- [ ] 创建 `nanobot/channels/feishu/__init__.py`
  ```python
  from nanobot.channels.feishu.bot import FeishuBot
  from nanobot.channels.feishu.config import FeishuConfig
  
  __all__ = ["FeishuBot", "FeishuConfig"]
  ```

### 4.2 配置更新

- [ ] 更新 `nanobot/config.py`
  ```python
  class FeishuConfig(BaseModel):
      # 原有字段
      app_id: str = ""
      app_secret: str = ""
      encrypt_key: str = ""
      verification_token: str = ""
      
      # 新增字段
      dm_policy: str = "pairing"
      group_policy: str = "open"
      require_mention: bool = False
      allow_from: list[str] = Field(default_factory=lambda: ["*"])
      
      session_scope: str = "group"
      reply_in_thread: bool = False
      
      debounce_enabled: bool = True
      debounce_ms: int = 300
      
      dedup_cache_size: int = 10000
      dedup_ttl_days: int = 7
  ```

### 4.3 兼容层（可选）

- [ ] 保留旧的 `nanobot/feishu.py` 作为兼容层
  ```python
  # 添加 deprecation warning
  import warnings
  warnings.warn(
      "nanobot.feishu is deprecated, use nanobot.channels.feishu",
      DeprecationWarning,
      stacklevel=2
  )
  
  # 从新的位置导入
  from nanobot.channels.feishu import FeishuBot
  ```

---

## Phase 5: 测试与验证 (预计 1-2 天)

### 5.1 单元测试

- [ ] 运行所有新测试
  ```bash
  pytest tests/channels/feishu/ -v
  ```

- [ ] 确保覆盖率 > 80%
  ```bash
  pytest --cov=nanobot.channels.feishu --cov-report=html
  ```

### 5.2 集成测试

- [ ] 创建 `tests/test_feishu_integration.py`
  - [ ] 测试完整消息流程
  - [ ] 测试并发消息处理
  - [ ] 测试重连场景
  - [ ] 测试权限拒绝

### 5.3 手动验证

- [ ] 启动 Bot，发送测试消息
- [ ] 验证消息去重（重复发送同一消息）
- [ ] 验证消息顺序（快速发送多条）
- [ ] 验证防抖（粘贴长文本）
- [ ] 验证配对流程（新用户）
- [ ] 验证会话隔离（不同策略）

---

## Phase 6: 部署与监控 (预计 1 天)

### 6.1 配置迁移

- [ ] 备份现有配置
  ```bash
  cp ~/.nanobot/config.json ~/.nanobot/config.json.bak
  ```

- [ ] 添加新配置项
  ```bash
  # 使用 jq 或直接编辑
  cat ~/.nanobot/config.json | jq '.feishu += {
      "dm_policy": "pairing",
      "session_scope": "group",
      "debounce_enabled": true
  }'
  ```

### 6.2 日志监控

- [ ] 添加关键指标日志
  ```python
  logger.info("消息处理统计", extra={
      "total_messages": stats["total"],
      "dedup_hits": stats["dedup_hits"],
      "avg_process_time": stats["avg_time"]
  })
  ```

- [ ] 配置日志收集（可选）

### 6.3 健康检查

- [ ] 添加健康检查端点
  ```python
  async def health_check():
      return {
          "status": "ok",
          "queue_size": len(bot._queue._queues),
          "sessions": len(bot._session_manager._sessions),
          "dedup_cache": len(bot._dedup._cache)
      }
  ```

---

## 风险与回滚

### 风险点

| 风险 | 可能性 | 影响 | 缓解措施 |
|-----|--------|------|---------|
| SQLite 性能瓶颈 | 中 | 高 | WAL 模式、批量写入、连接池 |
| 消息延迟增加 | 低 | 中 | 队列监控、超时设置 |
| 配置不兼容 | 低 | 高 | 向后兼容、默认配置 |

### 回滚方案

```bash
# 1. 停止新服务
pkill -f "nanobot run"

# 2. 恢复配置
cp ~/.nanobot/config.json.bak ~/.nanobot/config.json

# 3. 回滚代码
git checkout main -- nanobot/feishu.py

# 4. 启动旧服务
nanobot run
```

---

## 进度追踪

| Phase | 预计 | 实际 | 状态 |
|-------|------|------|------|
| 1. 基础设施 | 1-2 天 | - | ⬜ |
| 2. 核心功能 | 2-3 天 | - | ⬜ |
| 3. 整合 | 2 天 | - | ⬜ |
| 4. 新 Bot | 2 天 | - | ⬜ |
| 5. 测试 | 1-2 天 | - | ⬜ |
| 6. 部署 | 1 天 | - | ⬜ |

**总计预计:** 9-13 天

---

## 代码审查检查项

提交前请确认：

- [ ] 所有新文件都有文档字符串
- [ ] 类型注解完整
- [ ] 错误处理完善（try/except + 日志）
- [ ] 单元测试覆盖主要路径
- [ ] 无硬编码的敏感信息
- [ ] 配置项有合理的默认值
- [ ] 向后兼容（如果适用）

---

## 后续优化（Backlog）

- [ ] 支持 Webhook 模式（当前仅 WebSocket）
- [ ] 消息编辑功能（PATCH 消息）
- [ ] 表情反应（Reactions）
- [ ] 多账号支持
- [ ] 消息限流（Rate Limiting）
- [ ] 消息重试机制
- [ ] 死信队列（处理失败消息）
- [ ] 指标监控（Prometheus）
