"""Pydantic 请求 / 响应模型。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class RemediateRequest(BaseModel):
    command: str = Field(..., description="用户执行的原始命令")
    stderr: str = Field("", description="命令执行后的标准错误输出")
    stdout: str = Field("", description="命令执行后的标准输出")
    return_code: int = Field(..., description="命令返回码")
    environment_id: str = Field("default", description="环境标识（用于多租户隔离）")
    cwd: str = Field(".", description="当前工作目录")
    confirm_high_risk: bool = Field(False, description="是否自动确认高风险操作")


class RemediateResponse(BaseModel):
    status: Literal["success", "failed", "blocked", "needs_confirmation"]
    original_command: str
    fixed_command: Optional[str] = None
    root_cause: Optional[str] = None
    risk_level: Optional[str] = None
    confidence_score: Optional[float] = None
    message: Optional[str] = None
    metrics: Optional[dict[str, Any]] = None
