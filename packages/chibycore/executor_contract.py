"""Phase 0：统一执行与闭环数据结构（契约层，与 PTY/WebSocket 解耦）。

非交互单次执行场景使用；交互式会话仍复用 SessionManager.shell_input。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Literal, Optional, Protocol

TransportType = Literal["ssh", "winrm", "local"]


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class RunOptions:
    timeout_sec: float = 120.0
    working_directory: Optional[str] = None
    #: 若设置则边执行边回调子进程输出：``(stream, chunk)``，``stream`` 为 ``stdout`` / ``stderr``
    stream_chunk: Optional[Callable[[str, str], None]] = None


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: Optional[int]
    transport: str  # ssh | winrm | local（与 TransportType 对齐，存为 str 避免 Literal 运行期构造）
    duration_ms: int
    trace_id: str
    command: str = ""
    meta: dict = field(default_factory=dict)
    #: 输出是否因长度上限被截断（便于审计 / LLM）
    truncated: bool = False
    #: 单行摘要：超时、异常或非零退出时的可读原因（模型侧优先读）
    error_summary: Optional[str] = None

    @property
    def success(self) -> bool:
        if self.exit_code is None:
            return False
        try:
            return int(self.exit_code) == 0
        except (TypeError, ValueError):
            return False

    def to_tool_envelope(self) -> Dict[str, Any]:
        """与 MiniCC ToolResult 对齐的稳定信封：success / 文本输出 / 错误字段。"""
        err_line = self.error_summary
        if not err_line and not self.success:
            err_line = (self.stderr or "").strip() or None
        return {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "truncated": self.truncated,
            "transport": self.transport,
            "duration_ms": self.duration_ms,
            "error": err_line,
            "command": self.command[:4096] if self.command else "",
        }


@dataclass
class ClosurePayload:
    """一次执行结束后供 LLM / 归档 / 知识库的统一包。"""

    trace_id: str
    raw_command: str
    effective_command: str
    transport: str
    risk_level: RiskLevel
    exit_code: Optional[int]
    stdout: str
    stderr: str
    nl_intent_hint: Optional[str] = None
    session_id: Optional[str] = None
    plan_id: Optional[str] = None

    def to_audit_dict(self) -> dict:
        return {
            "event": "closure_payload",
            "trace_id": self.trace_id,
            "transport": self.transport,
            "risk_level": self.risk_level.value,
            "exit_code": self.exit_code,
            "raw_command": self.raw_command[:2048],
            "effective_command": self.effective_command[:2048],
        }


class UnifiedExecutor(Protocol):
    """Connect / RunCommand / Close 语义契约（同步）；异步可在调用方线程池封装。"""

    def connect(self) -> None: ...
    def run_command(self, command: str, options: Optional[RunOptions] = None) -> ExecResult: ...
    def close(self) -> None: ...
