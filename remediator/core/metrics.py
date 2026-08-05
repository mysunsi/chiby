"""Phase 5：可观测性 — 指标模型与 JSON Lines 收集器。"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class RemediationMetrics:
    """单次自愈会话的可观测指标（写入 metrics.jsonl）。"""

    session_id: str
    original_command: str
    kb_hit: bool
    llm_calls: int
    retries: int
    success: bool
    risk_blocked: bool
    error_category: str
    duration_ms: int
    dry_run: bool = False
    termination: str = ""
    """RemediationTerminationReason.value，dry_run 或拦截时可为空。"""
    fix_type: str = ""
    """空字符串 | lite | kb | … 标识修复路径（Phase 7.1 lite_fixer 等）。"""


class MetricsCollector:
    """将指标以 JSON Lines 追加到本地文件；写入失败绝不向外抛。"""

    def __init__(self, jsonl_path: Path | str | None = None) -> None:
        import os

        p = jsonl_path or os.environ.get("REMEDIATION_METRICS_PATH", "").strip()
        self._path = Path(p) if p else Path("data") / "remediation_metrics.jsonl"
        self._lock = threading.Lock()

    def append(self, metrics: RemediationMetrics) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(asdict(metrics), ensure_ascii=False) + "\n"
            with self._lock:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
        except OSError as e:
            logger.warning("指标写入失败（已忽略）: %s", e)
        except Exception as e:  # pragma: no cover
            logger.warning("指标序列化/写入异常（已忽略）: %s", e)

    @staticmethod
    def safe_append(
        collector: Optional["MetricsCollector"],
        metrics: RemediationMetrics,
    ) -> None:
        if collector is None:
            return
        collector.append(metrics)


def count_fix_retries(history: Any) -> int:
    """从 RemediationHistory 统计实际下发的修正命令次数。"""
    try:
        segments = getattr(history, "segments", None) or []
        return sum(1 for s in segments if getattr(s, "kind", None) == "fix_command")
    except Exception:
        return 0


__all__ = ["RemediationMetrics", "MetricsCollector", "count_fix_retries"]
