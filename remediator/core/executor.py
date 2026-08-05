"""
最小可替换执行器：默认用 subprocess。

生产环境可改用 ``ExecutorBackend`` 自定义实现；``run_command`` 等价于
``LocalSubprocessBackend().run``。
"""
from __future__ import annotations

from remediator.core.executor_backends import (
    DockerExecBackend,
    ExecutorBackend,
    ExecutorResult,
    LocalSubprocessBackend,
)

_default_backend = LocalSubprocessBackend()


def run_command(command: str, *, timeout: int = 300) -> ExecutorResult:
    """执行 shell 命令字符串；与 executor_wrapper 对接。"""
    return _default_backend.run(command, timeout=timeout)


__all__ = [
    "DockerExecBackend",
    "ExecutorBackend",
    "ExecutorResult",
    "LocalSubprocessBackend",
    "run_command",
]
