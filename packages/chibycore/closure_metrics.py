"""闭环（closure-retry）可观测性：成功率、尝试次数、人工打断、按主机标签聚合。

设计目标：
- 进程内线程安全计数器，便于日后 exporter 映射到 Prometheus（命名与 label 约定一致）。
- 用数据驱动调 prompt / 策略 / KB，而非仅 Demo 日志。
"""
from __future__ import annotations

import re
import threading
from typing import Any, Dict, List, Optional, Sequence

_LOCK = threading.Lock()
_registry: Dict[str, int] = {}


def _sanitize_entrypoint(ep: str) -> str:
    s = (ep or "").strip().lower()
    if not s:
        return "_unknown"
    s = re.sub(r"[^a-z0-9_]+", "_", s).strip("_")[:48]
    return s or "_unknown"


def _normalize_host_tags(tags: Optional[Sequence[str]]) -> List[str]:
    if not tags:
        return ["_untagged"]
    out = sorted({str(t).strip().lower() for t in tags if str(t).strip()})[:16]
    return out if out else ["_untagged"]


def _inc(key: str, n: int = 1) -> None:
    with _LOCK:
        _registry[key] = _registry.get(key, 0) + n


def record_closure_run(
    *,
    ok: bool,
    stop_reason: str,
    execute_attempts: int,
    fix_rounds_consumed: int,
    host_tags: Optional[Sequence[str]] = None,
    entrypoint: str = "",
) -> None:
    """单次闭环结束打点（与一次 ClosureRunResult 对齐）。"""
    ep = _sanitize_entrypoint(entrypoint)
    tags = _normalize_host_tags(host_tags)
    sr = (stop_reason or "_empty")[:120]

    if ok:
        outcome = "success"
    elif stop_reason == "user_cancelled":
        outcome = "user_cancelled"
    else:
        outcome = "failure"

    _inc("closure_runs_total")
    _inc(f"closure_runs_total|outcome={outcome}")
    _inc(f"closure_runs_total|outcome={outcome}|entrypoint={ep}")
    _inc(f"closure_runs_total|outcome={outcome}|stop_reason={sr}")

    if outcome == "success":
        _inc("closure_success_total")
        _inc(f"closure_success_total|entrypoint={ep}")
    elif outcome == "user_cancelled":
        _inc("closure_user_interrupt_total")
        _inc(f"closure_user_interrupt_total|entrypoint={ep}")

    _inc("closure_execute_attempts_total", execute_attempts)
    _inc(f"closure_execute_attempts_total|entrypoint={ep}", execute_attempts)
    _inc("closure_fix_rounds_consumed_total", fix_rounds_consumed)
    _inc(f"closure_fix_rounds_consumed_total|entrypoint={ep}", fix_rounds_consumed)

    for tag in tags:
        _inc(f"closure_runs_by_host_tag|tag={tag}|outcome={outcome}")
        _inc(f"closure_runs_by_host_tag|tag={tag}|entrypoint={ep}|outcome={outcome}")
        if outcome == "success":
            _inc(f"closure_success_by_host_tag|tag={tag}")


def snapshot_flat() -> Dict[str, int]:
    """扁平计数（便于 JSON 与自定义 exporter）。"""
    with _LOCK:
        return dict(_registry)


def snapshot_summary() -> Dict[str, Any]:
    """聚合视图：成功率、打断率、按标签成功次数（便于 Grafana 面板原型）。"""
    flat = snapshot_flat()
    runs = flat.get("closure_runs_total", 0)
    succ = flat.get("closure_success_total", 0)
    interrupt = flat.get("closure_user_interrupt_total", 0)
    exec_att = flat.get("closure_execute_attempts_total", 0)
    fix_cons = flat.get("closure_fix_rounds_consumed_total", 0)

    by_tag_success: Dict[str, int] = {}
    by_tag_runs: Dict[str, int] = {}
    for k, v in flat.items():
        if k.startswith("closure_success_by_host_tag|"):
            part = k.split("|", 1)[-1]
            if part.startswith("tag="):
                tag = part.split("=", 1)[1]
                by_tag_success[tag] = by_tag_success.get(tag, 0) + v
        if k.startswith("closure_runs_by_host_tag|") and "|outcome=" in k:
            if "|entrypoint=" in k:
                continue
            # closure_runs_by_host_tag|tag=X|outcome=Y
            parts = k.split("|")
            tag_part = next((p for p in parts if p.startswith("tag=")), "")
            oc_part = next((p for p in parts if p.startswith("outcome=")), "")
            if tag_part and oc_part:
                tag = tag_part.split("=", 1)[1]
                by_tag_runs[tag] = by_tag_runs.get(tag, 0) + v

    def _ratio(num: int, den: int) -> Optional[float]:
        if den <= 0:
            return None
        return round(num / den, 6)

    tag_success_ratio: Dict[str, Optional[float]] = {}
    for tag, r_count in by_tag_runs.items():
        if tag == "_untagged":
            continue
        s_count = by_tag_success.get(tag, 0)
        # runs per tag = sum of outcomes — approximate success ratio per tag
        tag_success_ratio[tag] = _ratio(s_count, r_count)

    return {
        "version": 1,
        "aggregate": {
            "closure_runs_total": runs,
            "closure_success_total": succ,
            "closure_user_interrupt_total": interrupt,
            "closure_execute_attempts_total": exec_att,
            "closure_fix_rounds_consumed_total": fix_cons,
            "repair_success_ratio": _ratio(succ, runs),
            "user_interrupt_ratio": _ratio(interrupt, runs),
            "avg_execute_attempts_per_run": _ratio(exec_att, runs),
            "avg_fix_rounds_consumed_per_run": _ratio(fix_cons, runs),
        },
        "by_host_tag": {
            "success_counts": by_tag_success,
            "run_counts_by_outcome_rollup": by_tag_runs,
            "repair_success_ratio_by_tag": tag_success_ratio,
        },
        "counters_flat_sample": dict(list(flat.items())[:80])
        if len(flat) > 80
        else flat,
    }


def prometheus_style_lines(max_lines: int = 200) -> List[str]:
    """预留：Prometheus text exposition 风格（注释行 + metric）。当前由扁平 key 映射。"""
    lines: List[str] = []
    lines.append("# HELP closure_runs_total Total closure runs (aggregated dimensions as suffix keys).")
    lines.append("# TYPE closure_runs_total counter")
    flat = snapshot_flat()
    n = 0
    for k, v in sorted(flat.items()):
        if n >= max_lines:
            lines.append(f"# ... truncated, total keys={len(flat)}")
            break
        safe_k = k.replace('"', "\\")
        lines.append('closure_flat{key="' + safe_k + '"} ' + str(v))
        n += 1
    return lines


def reset_closure_metrics_for_tests() -> None:
    global _registry
    with _LOCK:
        _registry = {}
