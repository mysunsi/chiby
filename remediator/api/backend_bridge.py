"""
首次执行注入客户端上报的结果（不再重复跑失败命令），后续修正命令走真实本地 shell。
"""
from __future__ import annotations

from typing import Optional

from remediator.core.executor_backends import (
    ExecutorBackend,
    ExecutorResult,
    LocalSubprocessBackend,
)


class ClientObservedBackend(ExecutorBackend):
    """第一次 ``run`` 返回插件/CI 观测到的输出；之后委托 ``LocalSubprocessBackend``。"""

    def __init__(
        self,
        stdout: str,
        stderr: str,
        return_code: int,
        *,
        delegate: Optional[ExecutorBackend] = None,
    ) -> None:
        self._first_stdout = stdout or ""
        self._first_stderr = stderr or ""
        self._first_rc = int(return_code)
        self._delegate = delegate or LocalSubprocessBackend()
        self._phase = 0

    def run(self, command: str, *, timeout: int = 300) -> ExecutorResult:
        if self._phase == 0:
            self._phase += 1
            return ExecutorResult(
                command=command,
                stdout=self._first_stdout,
                stderr=self._first_stderr,
                return_code=self._first_rc,
            )
        return self._delegate.run(command, timeout=timeout)


__all__ = ["ClientObservedBackend"]
