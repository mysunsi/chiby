"""自愈 HTTP API 路由。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from remediator.api.backend_bridge import ClientObservedBackend
from remediator.api.deps import build_environment_snapshot, verify_api_key
from remediator.api.schemas import RemediateRequest, RemediateResponse
from remediator.core.executor_wrapper import analyze_only, run_with_remediation
from remediator.core.risk_levels import infer_risk_level

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Remediation"])


def _payload_from_stdout(stdout: str) -> Dict[str, Any]:
    try:
        d = json.loads(stdout or "{}")
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def map_outcome_to_response(original_command: str, outcome: Any) -> RemediateResponse:
    """将 ``CommandExecutionOutcome`` 映射为统一响应。"""
    payload = _payload_from_stdout(outcome.stdout)
    stderr_tag = outcome.stderr or ""

    fixed_command = payload.get("final_command")
    if isinstance(fixed_command, str):
        pass
    elif fixed_command is not None:
        fixed_command = str(fixed_command)
    else:
        fixed_command = None

    termination = str(payload.get("termination") or "")
    message = payload.get("message")
    msg_str = message if isinstance(message, str) else None

    cc = outcome.confidence_score
    conf = float(cc) if cc is not None else None

    metrics_base: Dict[str, Any] = {
        "return_code": outcome.return_code,
        "termination": termination or None,
        "stderr_tag": stderr_tag[:500],
    }

    # HIGH / 规则 / 策略拦截
    if payload.get("blocked") or payload.get("rule_blocked"):
        return RemediateResponse(
            status="blocked",
            original_command=original_command,
            fixed_command=fixed_command,
            root_cause=str(payload.get("reason") or msg_str or ""),
            risk_level=str(payload.get("risk_level") or infer_risk_level(original_command, "").value),
            confidence_score=conf,
            message=msg_str,
            metrics={**metrics_base, "payload": payload},
        )

    if stderr_tag == "RULE_BLOCKED" or "POLICY_BLOCK" in stderr_tag:
        return RemediateResponse(
            status="blocked",
            original_command=original_command,
            fixed_command=fixed_command,
            root_cause=msg_str or stderr_tag,
            risk_level=infer_risk_level(original_command, "").value,
            confidence_score=conf,
            message=msg_str or stderr_tag,
            metrics=metrics_base,
        )

    # Lite / 控制器侧置信度拦截
    if stderr_tag == "CONFIDENCE_TOO_LOW" or payload.get("low_confidence"):
        pc = payload.get("proposed_command")
        return RemediateResponse(
            status="needs_confirmation",
            original_command=original_command,
            fixed_command=str(pc) if pc is not None else fixed_command,
            root_cause=str(payload.get("reason") or "") or msg_str,
            risk_level=infer_risk_level(str(pc or original_command), "").value,
            confidence_score=float(payload["confidence_score"]) if payload.get("confidence_score") is not None else conf,
            message=msg_str or "置信度不足，请人工确认后再执行 proposed_command",
            metrics={**metrics_base, "threshold": payload.get("threshold")},
        )

    risk_fix_cmd = fixed_command or original_command
    risk_level = infer_risk_level(str(risk_fix_cmd), "").value

    if termination == "success" and outcome.return_code == 0:
        return RemediateResponse(
            status="success",
            original_command=original_command,
            fixed_command=fixed_command,
            root_cause=msg_str,
            risk_level=risk_level,
            confidence_score=conf,
            message=msg_str,
            metrics=metrics_base,
        )

    if termination == "low_confidence" or outcome.return_code == 4:
        return RemediateResponse(
            status="needs_confirmation",
            original_command=original_command,
            fixed_command=fixed_command,
            root_cause=msg_str,
            risk_level=risk_level,
            confidence_score=conf,
            message=msg_str,
            metrics=metrics_base,
        )

    return RemediateResponse(
        status="failed",
        original_command=original_command,
        fixed_command=fixed_command,
        root_cause=msg_str,
        risk_level=risk_level,
        confidence_score=conf,
        message=msg_str,
        metrics=metrics_base,
    )


def map_analyze_report(original_command: str, report: Dict[str, Any]) -> RemediateResponse:
    st = report.get("status")
    if st == "success":
        return RemediateResponse(
            status="success",
            original_command=original_command,
            message=str(report.get("message") or "命令已成功，无需修复建议。"),
            risk_level=str(report.get("risk_level_initial") or infer_risk_level(original_command, "").value),
            metrics={"analyze_status": st},
        )
    if st == "skipped_probe":
        return RemediateResponse(
            status="failed",
            original_command=original_command,
            message=str(report.get("message") or ""),
            metrics={"analyze_status": st},
        )
    if st == "error":
        return RemediateResponse(
            status="failed",
            original_command=original_command,
            message=str(report.get("message") or "analyze error"),
            metrics={"analyze_status": st},
        )
    if st == "analysis_partial":
        return RemediateResponse(
            status="failed",
            original_command=original_command,
            message=str(report.get("error") or ""),
            metrics=report,
        )

    proposal = report.get("proposal") or {}
    fixed = proposal.get("fixed_command") if isinstance(proposal, dict) else None
    root = proposal.get("root_cause") if isinstance(proposal, dict) else None
    rw = proposal.get("risk_warning") if isinstance(proposal, dict) else ""
    risk_fix = str(
        report.get("risk_level_fix") or infer_risk_level(str(fixed or ""), str(rw or "")).value
    )
    pconf = proposal.get("confidence_score") if isinstance(proposal, dict) else None

    return RemediateResponse(
        status="needs_confirmation",
        original_command=original_command,
        fixed_command=str(fixed) if fixed else None,
        root_cause=str(root) if root else None,
        risk_level=risk_fix,
        confidence_score=float(pconf) if pconf is not None else None,
        message="分析完成（未执行修复）；请确认 fixed_command 后再下发。",
        metrics={
            "analyze_status": st,
            "proposal_source": report.get("proposal_source"),
            "fixable": report.get("fixable"),
            "fixability_reason": report.get("fixability_reason"),
        },
    )


@router.post("/remediate", response_model=RemediateResponse)
async def remediate_command(
    request: RemediateRequest,
    _api_key: str = Depends(verify_api_key),
):
    env = build_environment_snapshot(request)
    backend = ClientObservedBackend(
        request.stdout,
        request.stderr,
        request.return_code,
    )

    def _run():
        return run_with_remediation(
            request.command,
            backend=backend,
            dry_run=False,
            interactive=False,
            confirm_high_risk=request.confirm_high_risk,
            env=env,
            write_diagnostic_reports=False,
        )

    try:
        outcome = await asyncio.to_thread(_run)
    except Exception as e:
        logger.exception("remediate failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    return map_outcome_to_response(request.command, outcome)


@router.post("/analyze", response_model=RemediateResponse)
async def analyze_command(
    request: RemediateRequest,
    _api_key: str = Depends(verify_api_key),
):
    env = build_environment_snapshot(request)
    backend = ClientObservedBackend(
        request.stdout,
        request.stderr,
        request.return_code,
    )

    def _run():
        return analyze_only(
            request.command,
            backend=backend,
            env=env,
            execute_probe=True,
        )

    try:
        report = await asyncio.to_thread(_run)
    except Exception as e:
        logger.exception("analyze failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail="analyze_only returned invalid type")
    return map_analyze_report(request.command, report)
