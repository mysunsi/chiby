"""闭环与网关可观测性的轻量内部事件（便于 SSE / WS / Prometheus 扩展）。"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional

from chibycore.closure_retry_runner import ClosureStepRecord


SCHEMA_VERSION: Literal[1] = 1


@dataclass(frozen=True)
class ClosureObsEvent:
    """统一外壳：序列化后可直接写入 SSE ``obs`` 帧或 WS JSON。"""

    schema_version: int
    kind: str
    trace_id: str
    payload: Dict[str, Any]


def closure_obs_step(trace_id: str, record: ClosureStepRecord) -> ClosureObsEvent:
    """单步结束（含网关结果与 ExecResult 信封）。"""
    pl: Dict[str, Any] = {
        "phase": record.phase,
        "fix_round": record.fix_round,
        "gateway_allowed": record.gateway_allowed,
        "gateway_reason": (record.gateway_reason or "")[:2000],
        "gateway_detail": record.gateway_detail or {},
        "pending_change_control": record.pending_change_control,
        "exit_ok": record.exit_ok,
        "llm_judge_ok": record.llm_judge_ok,
        "llm_judge_reason": (record.llm_judge_reason or "")[:2000],
        "outcome_detail": (record.outcome_detail or "")[:4000],
    }
    if record.result is not None:
        pl["exec"] = record.result.to_tool_envelope()
    return ClosureObsEvent(
        schema_version=SCHEMA_VERSION,
        kind="closure_step",
        trace_id=trace_id,
        payload=pl,
    )


def closure_obs_span(
    trace_id: str,
    kind: str,
    *,
    detail: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> ClosureObsEvent:
    """生命周期锚点：如 closure_begin / gateway_precheck / closure_done。"""
    pl: Dict[str, Any] = {}
    if detail:
        pl["detail"] = detail[:4000]
    if extra:
        pl.update(extra)
    return ClosureObsEvent(
        schema_version=SCHEMA_VERSION,
        kind=kind,
        trace_id=trace_id,
        payload=pl,
    )


def obs_event_to_ws_dict(ev: ClosureObsEvent) -> Dict[str, Any]:
    return asdict(ev)
