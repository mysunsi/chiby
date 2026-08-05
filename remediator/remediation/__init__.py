"""错误迭代修正流程（remediation）。

重依赖（litellm）通过 __getattr__ 延迟加载，便于仅使用 KB / parser / models 的场景。
"""
from __future__ import annotations

import importlib
from typing import Any, List

from .knowledge_base import RemediationKnowledgeBase
from .models import (
    CommandExecutionOutcome,
    EnvironmentSnapshot,
    ErrorCategory,
    KnowledgeRecord,
    LLMRemediationJSON,
    RemediationHistory,
    RemediationSessionResult,
    RemediationTerminationReason,
    StructuredError,
    compute_error_fingerprint,
    normalize_command_for_fingerprint,
    normalize_text_for_fingerprint,
    os_fingerprint_key,
)
from .parser import assess_fixability, parse_execution_error

__all__ = [
    "propose_remediation",
    "RemediationKnowledgeBase",
    "RemediationController",
    "build_default_kb_path",
    "command_similarity",
    "levenshtein_distance",
    "levenshtein_ratio",
    "CommandExecutionOutcome",
    "EnvironmentSnapshot",
    "ErrorCategory",
    "KnowledgeRecord",
    "LLMRemediationJSON",
    "RemediationHistory",
    "RemediationSessionResult",
    "RemediationTerminationReason",
    "StructuredError",
    "compute_error_fingerprint",
    "normalize_command_for_fingerprint",
    "normalize_text_for_fingerprint",
    "os_fingerprint_key",
    "assess_fixability",
    "parse_execution_error",
]

_LAZY = {
    "propose_remediation": (".llm_agent", "propose_remediation"),
    "RemediationController": (".loop", "RemediationController"),
    "build_default_kb_path": (".loop", "build_default_kb_path"),
    "command_similarity": (".loop", "command_similarity"),
    "levenshtein_distance": (".loop", "levenshtein_distance"),
    "levenshtein_ratio": (".loop", "levenshtein_ratio"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        mod_name, attr = _LAZY[name]
        mod = importlib.import_module(mod_name, package=__name__)
        return getattr(mod, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> List[str]:
    return sorted(set(list(globals()) + __all__))
