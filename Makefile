.PHONY: lint format typecheck test ci clean docker docker-up docker-down run web chat dev

# 静态检查
lint:
	uv run ruff check src/ tests/

# 格式化
format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

# 类型检查
typecheck:
	uv run mypy src/ccbot/

# 单元测试
test:
	uv run pytest tests/ -v

# CI 全流程（lint + typecheck + test）
ci: lint typecheck test

# ── 运行 ──

# 启动机器人（默认 feishu 通道 + 嵌入 Web 控制台 :8787）
run:
	uv run ccbot run

# 仅启动独立 Web 控制台（离线配置管理）
web:
	uv run ccbot web

# CLI 交互模式
chat:
	uv run ccbot chat

# 开发模式：CLI 通道 + Web 控制台
dev:
	uv run ccbot run --channel cli

# Docker
docker:
	docker build -t ccbot:latest .

docker-up:
	docker compose up -d

docker-down:
	docker compose down

# 清理缓存
clean:
	rm -rf .mypy_cache .pytest_cache .ruff_cache __pycache__
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
