"""执行后端抽象：本地 subprocess、Docker exec 等（与 remediation 解耦）。"""
from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ExecutorResult:
    """单次命令执行结果（逻辑命令与进程输出）。"""

    command: str
    stdout: str
    stderr: str
    return_code: int


class ExecutorBackend(ABC):
    """命令执行后端：SSH、本地 shell、容器内 exec 等均可实现此接口。"""

    @abstractmethod
    def run(self, command: str, *, timeout: int = 300) -> ExecutorResult:
        """执行 shell 命令字符串，返回结构化结果。"""


class LocalSubprocessBackend(ExecutorBackend):
    """默认后端：本机 `subprocess.run(..., shell=True)`。"""

    def run(self, command: str, *, timeout: int = 300) -> ExecutorResult:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ExecutorResult(
            command=command,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            return_code=int(proc.returncode),
        )


class DockerExecBackend(ExecutorBackend):
    """
    在指定容器内执行命令：`docker exec <container_id> sh -c "<command>"`。

    ``container_id`` 应为可信输入（由调用方校验）。
    """

    def __init__(self, container_id: str) -> None:
        self._container_id = container_id

    def run(self, command: str, *, timeout: int = 300) -> ExecutorResult:
        proc = subprocess.run(
            ["docker", "exec", self._container_id, "sh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return ExecutorResult(
            command=command,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            return_code=int(proc.returncode),
        )


__all__ = [
    "DockerExecBackend",
    "ExecutorBackend",
    "ExecutorResult",
    "LocalSubprocessBackend",
]
