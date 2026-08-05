"""执行网关：策略评估 + 变更冻结窗口 + 审计 + 指标（工业级 P0）。"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from chibycore.audit_log import append_audit
from chibycore.change_window import (
    change_window_enabled_globally,
    is_change_window_frozen,
)
from chibycore.metrics import get_gateway_metrics
from chibycore.policy_engine import get_policy_engine, policy_enabled
from chibycore.redaction import redact_command_text, redact_host_hint

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRequest:
    trace_id: str
    session_id: str
    command_line: str
    source: str  # ws_exec | ws_llm_auto | ws_confirm | ws_plan | ws_verify | ws_broadcast | intent_broadcast
    conn_type: str = "local"
    host_id: Optional[str] = None
    plan_id: Optional[str] = None
    #: 审批通过后重放执行时跳过变更窗口冻结（仍走策略黑名单）
    change_window_bypass: bool = False
    #: 为 True 时仅做策略/冻结判定，不写审计、不增指标（用于人机共编前的网关预览）
    preview: bool = False


@dataclass
class ExecutionOutcome:
    allowed: bool
    reason: str = ""
    #: True 表示命中「变更冻结窗口」，应入待审批队列而非策略拒绝
    pending_change_control: bool = False
    #: 入队后的 pending_id（仅 hold 时有值）
    pending_id: str = ""
    #: policy_deny | change_window_hold | ""（放行或无类别）
    denial_category: str = ""
    #: 与 PolicyResult.rule_kind / change_window 等的同源标签
    rule_kind: str = ""
    #: 策略命中时的模式串（变更窗口为空）
    matched_pattern: str = ""
    #: 是否必须通过审批（或双人确认等产品策略）才能覆盖本次拒绝
    override_requires_approval: bool = False
    #: 渐进式放行占位：如 dual_confirm_candidate | session_allow_candidate（未实现具体状态机时可留空）
    progressive_policy_hint: str = ""


@dataclass(frozen=True)
class GatewayAllowResult:
    """供闭环 ``gateway_allow`` 回调返回：策略拒绝 vs 变更窗口待审批。"""

    allowed: bool
    reason: str = ""
    pending_change_control: bool = False
    #: 入队后的 id（仅 ``pending_change_control`` 时有值）
    pending_id: str = ""
    denial_category: str = ""
    rule_kind: str = ""
    matched_pattern: str = ""
    override_requires_approval: bool = False
    progressive_policy_hint: str = ""


def pack_gateway_allow(out: ExecutionOutcome, *, pending_id: str = "") -> GatewayAllowResult:
    return GatewayAllowResult(
        allowed=out.allowed,
        reason=out.reason or "",
        pending_change_control=out.pending_change_control,
        pending_id=pending_id or getattr(out, "pending_id", "") or "",
        denial_category=out.denial_category or "",
        rule_kind=out.rule_kind or "",
        matched_pattern=out.matched_pattern or "",
        override_requires_approval=out.override_requires_approval,
        progressive_policy_hint=out.progressive_policy_hint or "",
    )


def gateway_allow_detail(g: GatewayAllowResult) -> Dict[str, Any]:
    """网关拒绝时的可解释性载荷（API / 闭环步骤 / 前端标签）。"""
    if g.allowed:
        return {}
    cat = (g.denial_category or "").strip()
    if not cat:
        cat = "change_window_hold" if g.pending_change_control else "policy_deny"
    out: Dict[str, Any] = {
        "denial_category": cat,
        "rule_kind": (g.rule_kind or "").strip(),
        "matched_pattern": (g.matched_pattern or "").strip(),
        "override_requires_approval": bool(
            g.override_requires_approval or g.pending_change_control
        ),
    }
    hint = (g.progressive_policy_hint or "").strip()
    if hint:
        out["progressive_policy_hint"] = hint
    return out


def execution_outcome_detail(out: ExecutionOutcome) -> Dict[str, Any]:
    """与 :func:`gateway_allow_detail` 一致，用于尚未打包为 ``GatewayAllowResult`` 的路径。"""
    if out.allowed:
        return {}
    cat = (out.denial_category or "").strip()
    if not cat:
        cat = "change_window_hold" if out.pending_change_control else "policy_deny"
    d: Dict[str, Any] = {
        "denial_category": cat,
        "rule_kind": (out.rule_kind or "").strip(),
        "matched_pattern": (out.matched_pattern or "").strip(),
        "override_requires_approval": bool(
            out.override_requires_approval or out.pending_change_control
        ),
    }
    hint = (out.progressive_policy_hint or "").strip()
    if hint:
        d["progressive_policy_hint"] = hint
    return d


_FREEZE_REASON = "变更冻结窗口内：请先审批后再执行（治理节奏）"


def gateway_evaluate(req: ExecutionRequest) -> ExecutionOutcome:
    """
    同步评估；调用方在拒绝时应向用户返回 reason，且不执行 shell。
    顺序：策略黑名单 → 变更冻结窗口 → 放行。
    ``preview=True`` 时不写审计、不增指标（修复意图预览 / 人机共编）。
    """
    pv = bool(getattr(req, "preview", False))
    metrics = get_gateway_metrics()
    engine = get_policy_engine()
    redacted = redact_command_text(req.command_line)
    host_hint = redact_host_hint(req.host_id, None)

    # ── 1) 策略黑名单（启用时）──────────────────────────────────────
    if policy_enabled():
        pr = engine.evaluate_line(req.command_line)
        if not pr.allowed:
            if not pv:
                metrics.inc("gateway_deny")
                append_audit(
                    {
                        "event": "execution_gateway",
                        "decision": "deny",
                        "reason": pr.reason,
                        "trace_id": req.trace_id,
                        "session_id": req.session_id,
                        "source": req.source,
                        "conn_type": req.conn_type,
                        "host": host_hint,
                        "plan_id": req.plan_id,
                        "command_redacted": redacted,
                    }
                )
            return ExecutionOutcome(
                False,
                pr.reason or "策略拒绝执行",
                pending_change_control=False,
                denial_category="policy_deny",
                rule_kind=getattr(pr, "rule_kind", "") or "deny_regex",
                matched_pattern=getattr(pr, "matched_pattern", "") or "",
                override_requires_approval=False,
            )

    # ── 2) 变更冻结窗口（非 bypass）─────────────────────────────────
    if (
        not req.change_window_bypass
        and change_window_enabled_globally()
        and is_change_window_frozen()
    ):
        if not pv:
            metrics.inc("gateway_change_window_hold")
            append_audit(
                {
                    "event": "execution_gateway",
                    "decision": "hold_change_window",
                    "reason": _FREEZE_REASON,
                    "trace_id": req.trace_id,
                    "session_id": req.session_id,
                    "source": req.source,
                    "conn_type": req.conn_type,
                    "host": host_hint,
                    "plan_id": req.plan_id,
                    "command_redacted": redacted,
                }
            )
        pending_id = ""
        if not pv:
            try:
                from chibycore.pending_change_control import enqueue_pending_change

                pending_id = enqueue_pending_change(
                    trace_id=req.trace_id,
                    session_id=req.session_id,
                    command_line=req.command_line,
                    source=req.source,
                    conn_type=req.conn_type,
                    host_id=req.host_id,
                    plan_id=req.plan_id,
                )
            except Exception:
                logger.exception("变更冻结入队失败 trace_id=%s", req.trace_id)
        return ExecutionOutcome(
            False,
            _FREEZE_REASON,
            pending_change_control=True,
            pending_id=pending_id or "",
            denial_category="change_window_hold",
            rule_kind="change_window",
            matched_pattern="",
            override_requires_approval=True,
        )

    # ── 3) 放行 ───────────────────────────────────────────────────────
    if not policy_enabled():
        if not pv:
            metrics.inc("gateway_skip_policy")
            if os.environ.get("OPS_AUDIT_ALWAYS", "").strip() == "1":
                append_audit(
                    {
                        "event": "execution_gateway",
                        "decision": "allow",
                        "reason": "policy_disabled",
                        "trace_id": req.trace_id,
                        "session_id": req.session_id,
                        "source": req.source,
                        "conn_type": req.conn_type,
                        "host": host_hint,
                        "plan_id": req.plan_id,
                        "command_redacted": redacted,
                    }
                )
    else:
        if not pv:
            metrics.inc("gateway_allow")
            append_audit(
                {
                    "event": "execution_gateway",
                    "decision": "allow",
                    "trace_id": req.trace_id,
                    "session_id": req.session_id,
                    "source": req.source,
                    "conn_type": req.conn_type,
                    "host": host_hint,
                    "plan_id": req.plan_id,
                    "command_redacted": redacted,
                }
            )
    return ExecutionOutcome(True, "", pending_change_control=False)
