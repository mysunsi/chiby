"""生产执行层（可被替换为 SSH / API 等）。

``executor_wrapper`` 延迟导入，避免仅使用 executor_backends 时拉取 remediation/litellm。
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from .executor import (
    DockerExecBackend,
    ExecutorBackend,
    ExecutorResult,
    LocalSubprocessBackend,
    run_command,
)
from .metrics import MetricsCollector, RemediationMetrics, count_fix_retries

__all__ = [
    "DockerExecBackend",
    "ExecutorBackend",
    "ExecutorResult",
    "LocalSubprocessBackend",
    "run_command",
    "RiskLevel",
    "analyze_only",
    "infer_risk_level",
    "run_with_remediation",
    "RemediationMetrics",
    "MetricsCollector",
    "count_fix_retries",
]

_LAZY_EXECUTOR_WRAPPER = frozenset(
    {"RiskLevel", "analyze_only", "infer_risk_level", "run_with_remediation"}
)


def __getattr__(name: str) -> Any:
    if name in _LAZY_EXECUTOR_WRAPPER:
        mod = import_module(".executor_wrapper", __package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
