"""remediator：自愈核心与 FastAPI 封装（延迟导入，避免仅拉起 API 时拉全量依赖）。"""

from typing import Any

__all__ = ["run_with_remediation", "analyze_only"]


def __getattr__(name: str) -> Any:
    if name == "run_with_remediation":
        from remediator.core.executor_wrapper import run_with_remediation

        return run_with_remediation
    if name == "analyze_only":
        from remediator.core.executor_wrapper import analyze_only

        return analyze_only
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
