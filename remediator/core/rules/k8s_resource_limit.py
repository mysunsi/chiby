"""Kubernetes / 容器 OOM 场景下为修正命令追加资源参数（示例实现）。"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from remediator.core.rule_engine import BaseRule

if TYPE_CHECKING:
    from remediator.remediation.models import StructuredError


class K8sResourceLimitRule(BaseRule):
    """
    当结构化错误文本中出现 OOMKilled / OOM 等字样时，对修正命令追加 ``--memory=4Gi``
    （适用于 kubectl run / set resources 等场景的示例拼接）。
    """

    def should_trigger(self, error: Optional["StructuredError"]) -> bool:
        if error is None:
            return False
        blob = (
            (error.raw_stderr or "")
            + "\n"
            + (error.reason or "")
            + "\n"
            + (error.stderr_snippet or "")
        ).lower()
        return (
            "oomkilled" in blob
            or "oom killed" in blob
            or "out of memory" in blob
            or "memory limit" in blob
        )

    def process(self, command: str) -> str:
        c = command.strip()
        if "--memory=" in c:
            return command
        return f"{c} --memory=4Gi"
