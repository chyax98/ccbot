"""Agent 调度结构化模型。

替换 team.py 中的文本解析 <dispatch>...</dispatch> → 结构化 Pydantic
"""

from __future__ import annotations

import json
import re
from typing import Self

from pydantic import BaseModel, Field, field_validator

# 模块级别的正则，用于从文本提取 dispatch 块
_DISPATCH_RE: re.Pattern[str] = re.compile(r"<dispatch>\s*([\s\S]*?)\s*</dispatch>", re.IGNORECASE)


class WorkerTask(BaseModel):
    """Worker 任务定义。

    Attributes:
        name: Worker 唯一名称（如 frontend / backend / reviewer）
        task: 详细任务描述（必填）
        cwd: 工作目录，必须是绝对路径
        model: 模型名称，空则继承 Supervisor 配置
        max_turns: 最大对话轮数
    """

    name: str = Field(..., min_length=1, description="Worker 唯一名称")
    task: str = Field(..., min_length=1, description="详细任务描述")
    cwd: str = Field(default=".", description="工作目录")
    model: str = Field(default="", description="模型名称")
    max_turns: int = Field(default=30, ge=1, le=100, description="最大轮数")

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, v: str) -> str:
        """验证 cwd 为绝对路径（如果非默认值）。"""
        if v != "." and not v.startswith("/"):
            # 允许相对路径，但建议绝对路径
            pass
        return v


class DispatchPayload(BaseModel):
    """调度负载，包含多个 Worker 任务。

    Attributes:
        tasks: Worker 任务列表
    """

    tasks: list[WorkerTask] = Field(..., min_length=1, description="Worker 任务列表")

    @classmethod
    def from_text(cls, text: str) -> Self | None:
        """从文本解析 dispatch 块。

        Args:
            text: 可能包含 <dispatch>...</dispatch> 的文本

        Returns:
            DispatchPayload 或 None（如果没有 dispatch 块或解析失败）
        """
        match = _DISPATCH_RE.search(text)
        if not match:
            return None

        try:
            data = json.loads(match.group(1))
            if not isinstance(data, list):
                return None
            return cls(tasks=data)
        except (json.JSONDecodeError, ValueError):
            return None

    def to_json(self) -> str:
        """序列化为 JSON 字符串。"""
        return self.model_dump_json(indent=2)

    @property
    def worker_names(self) -> str:
        """返回逗号分隔的 worker 名称列表。"""
        return ", ".join(t.name for t in self.tasks)


class WorkerResult(BaseModel):
    """Worker 执行结果。

    Attributes:
        name: Worker 名称
        success: 是否成功
        result: 结果内容（成功时）
        error: 错误信息（失败时）
    """

    name: str
    success: bool
    result: str = ""
    error: str = ""

    @classmethod
    def from_exception(cls, name: str, exc: BaseException) -> Self:
        """从异常创建失败结果。"""
        return cls(name=name, success=False, error=str(exc))

    @classmethod
    def from_result(cls, name: str, result: str) -> Self:
        """从结果创建成功结果。"""
        return cls(name=name, success=True, result=result)


class DispatchResult(BaseModel):
    """完整调度结果。"""

    workers: list[WorkerResult]

    @property
    def all_succeeded(self) -> bool:
        """是否所有 Worker 都成功。"""
        return all(w.success for w in self.workers)

    @property
    def failed_workers(self) -> list[str]:
        """返回失败的 Worker 名称列表。"""
        return [w.name for w in self.workers if not w.success]

    def to_synthesis_prompt(self, original_request: str = "") -> str:
        """生成供 Supervisor 综合的提示词。"""
        lines = ["以下是各 worker 的执行结果，请综合后向用户汇报：\n"]
        if original_request:
            lines.append(f"用户原始请求：{original_request}\n")
        for worker in self.workers:
            if worker.success:
                lines.append(f"### [{worker.name}]\n{worker.result}\n")
            else:
                lines.append(f"### [{worker.name}] ❌ 执行失败\n错误: {worker.error}\n")
        return "\n".join(lines)
