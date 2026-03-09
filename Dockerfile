# ── ccbot Docker 镜像 ──
# claude-agent-sdk 内置了平台对应的 claude 二进制，不需要 Node.js
FROM python:3.11-slim AS base

# 系统依赖（git 供 Claude agent 使用）
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 非 root 用户
RUN useradd -m -s /bin/bash ccbot \
    && mkdir -p /home/ccbot/.ccbot/workspace /home/ccbot/.ccbot/data \
    && chown -R ccbot:ccbot /home/ccbot/.ccbot

WORKDIR /app

# ── 依赖安装（利用 Docker 缓存）──
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project

# ── 复制项目代码 ──
COPY src/ src/
RUN uv sync --no-dev

# 切换用户
USER ccbot

# 默认工作目录和配置
ENV CCBOT_AGENT__WORKSPACE=/home/ccbot/.ccbot/workspace
ENV PYTHONUNBUFFERED=1

# A2A 服务器端口
EXPOSE 8765

# 健康检查（飞书模式下检查进程存活即可）
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD pgrep -f "ccbot" > /dev/null || exit 1

ENTRYPOINT ["uv", "run", "ccbot"]
CMD ["run"]
