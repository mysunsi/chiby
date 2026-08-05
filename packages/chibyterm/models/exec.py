"""执行结果契约（掌上 / 无头执行面共用）。

与 ``chibycore.executor_contract.ExecResult``（oneshot 传输层 dataclass）区分：
本模块为编排侧 Pydantic 结果信封。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    pass

# 传输层 RunOptions / RiskLevel 仍以 chibycore 为准；此处再导出便于开源契约锚点发现。
from chibycore.executor_contract import RiskLevel, RunOptions  # noqa: F401


class ExecResult(BaseModel):
    """无头 / 掌上编排的单次命令执行结果。"""

    ok: bool
    host_id: str
    command: str
    exit_code: int = 0
    stdout_tail: str = ""
    stderr_tail: str = ""
    duration_ms: int = 0
    fake: bool = False
    error: str = ""
