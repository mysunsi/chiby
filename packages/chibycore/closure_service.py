"""Phase 3：结果闭环数据结构构建与有限重试状态（LLM 调用由上层注入）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

from chibycore.executor_contract import ClosurePayload, ExecResult, RiskLevel, TransportType
from chibycore.risk_heuristic import heuristic_risk_level


@dataclass
class RetryBudget:
    max_attempts: int = 3
    attempts: int = 0

    def can_retry(self) -> bool:
        return self.attempts < self.max_attempts

    def consume(self) -> None:
        self.attempts += 1


def build_closure_payload(
    *,
    trace_id: str,
    raw_command: str,
    effective_command: str,
    result: ExecResult,
    nl_intent_hint: Optional[str] = None,
    session_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    risk_override: Optional[RiskLevel] = None,
) -> ClosurePayload:
    rk = risk_override or heuristic_risk_level(effective_command or raw_command)
    return ClosurePayload(
        trace_id=trace_id,
        raw_command=raw_command,
        effective_command=effective_command,
        transport=result.transport,
        risk_level=rk,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        nl_intent_hint=nl_intent_hint,
        session_id=session_id,
        plan_id=plan_id,
    )


def success_for_closure(cp: ClosurePayload, success_exit_codes: Optional[List[int]] = None) -> bool:
    codes = success_exit_codes if success_exit_codes is not None else [0]
    if cp.exit_code is None:
        return False
    return cp.exit_code in codes


ClosureLLMCallable = Callable[[ClosurePayload], List[str]]
