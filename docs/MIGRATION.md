# ccbot v1 → v2 迁移指南

## 概述

ccbot v2 引入了重大架构改进：

- **新架构**: OpenClaw 风格的分层设计
- **新 Pipeline**: Dedup → Debounce → Queue 入站处理
- **新调度**: 结构化 Pydantic Dispatch 替代文本解析
- **新 Channel**: 抽象基类支持多通道

## 破坏性变更

### 1. 配置位置

**v1**:
```bash
~/.nanobot/config.json
```

**v2**:
```bash
~/.ccbot/config.json
```

**迁移**:
```bash
mkdir -p ~/.ccbot
mv ~/.nanobot/config.json ~/.ccbot/config.json
# 编辑配置：将 nanobot 相关路径改为 ccbot
```

### 2. 环境变量前缀

**v1**:
```bash
export NANOBOT_FEISHU__APP_ID=xxx
export NANOBOT_FEISHU__APP_SECRET=xxx
```

**v2**:
v2 使用 `CCBOT_` 前缀，不再支持 `NANOBOT_` 前缀：
```bash
export CCBOT_FEISHU__APP_ID=xxx
export CCBOT_FEISHU__APP_SECRET=xxx
```

### 3. Python 导入

**v1**:
```python
from nanobot.feishu import FeishuBot
from nanobot.agent import NanobotAgent
```

**v2**:
```python
from ccbot.feishu import FeishuBot      # 旧版兼容
from ccbot.channels.feishu import FeishuChannel  # 新版推荐
from ccbot.agent import CCBotAgent
from ccbot.channels.cli import CLIChannel
```

### 4. FeishuBot 初始化

**v1**:
```python
from ccbot.feishu import FeishuBot

bot = FeishuBot(config, on_message)
await bot.start()
```

**v2** (保持兼容):
```python
from ccbot.feishu import FeishuBot  # 仍可用

bot = FeishuBot(config, on_message)
await bot.start()
```

**v2** (新版推荐):
```python
from ccbot.channels.feishu import FeishuChannel

channel = FeishuChannel(config)
channel.on_message(handler)
await channel.start()
```

## 新增功能

### 1. CLI 交互模式

```bash
# 交互式
uv run python -m ccbot chat

# 单消息
uv run python -m ccbot chat -m "Hello"
```

### 2. 新配置选项

```json
{
  "feishu": {
    "use_v2_pipeline": true  // 启用新 Pipeline（默认 true）
  },
  "agent": {
    "heartbeat_enabled": true,
    "heartbeat_interval": 1800
  }
}
```

### 3. 结构化 Dispatch

Supervisor 输出格式保持不变：

```xml
<dispatch>
[
  {"name": "worker1", "task": "...", "cwd": "/path"}
]
</dispatch>
```

但内部现在使用 Pydantic 模型解析，更安全可靠。

## 迁移步骤

### 步骤 1: 更新依赖

```bash
git pull origin main
uv sync
```

### 步骤 2: 迁移配置

```bash
# 备份旧配置
cp ~/.nanobot/config.json ~/.nanobot/config.json.bak

# 迁移到新位置
mkdir -p ~/.ccbot
mv ~/.nanobot/config.json ~/.ccbot/config.json

# 可选：迁移 workspace
mv ~/.nanobot/workspace ~/.ccbot/workspace
```

### 步骤 3: 验证安装

```bash
# 检查版本
uv run python -m ccbot version

# 运行测试
uv run pytest tests/ -xvs

# 测试 CLI
uv run python -m ccbot chat -m "/help"
```

### 步骤 4: 启动飞书 Bot

```bash
# 方式 1：旧命令（兼容）
uv run python -m ccbot run

# 方式 2：显式指定配置
uv run python -m ccbot run --config ~/.ccbot/config.json
```

## 故障排除

### 问题：导入错误

```
ModuleNotFoundError: No module named 'nanobot'
```

**解决**: 更新导入语句为 `ccbot`。

### 问题：配置文件未找到

```
Error: 飞书 App ID 和 App Secret 未配置
```

**解决**: 确保配置文件在 `~/.ccbot/config.json`。

### 问题：去重缓存丢失

**解决**: v2 自动从 `~/.ccbot/dedup/` 加载缓存，首次运行会创建新文件。

## 回滚

如需回滚到 v1:

```bash
git checkout v1.x
uv sync
# 恢复旧配置文件位置
mv ~/.ccbot/config.json ~/.nanobot/config.json
```

## 反馈

遇到问题请在 GitHub Issues 反馈：
https://github.com/yourusername/ccbot/issues
