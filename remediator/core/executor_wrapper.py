"""
生产集成：在不调改 remediation 包内部代码的前提下，对接 core.executor 并支持 dry-run / 风险分级。

Phase 5：植入 MetricsCollector（JSON Lines），失败不影响主流程。
"""
from __future__ import annotations

import json
import logging
import os
import platform
import time
import types
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import remediator.remediation.loop as loop_mod

from remediator.core.executor_backends import (
    DockerExecBackend,
    ExecutorBackend,
    ExecutorResult,
    LocalSubprocessBackend,
)
from remediator.core.confidence import confidence_for_remediation_source
from remediator.core.diagnostics import DiagnosticBundle, write_diagnostic_report
from remediator.core.lite_fixer import try_lite_fix
from remediator.core.risk_levels import RiskLevel, infer_risk_level
from remediator.core.metrics import (
    MetricsCollector,
    RemediationMetrics,
    count_fix_retries,
)
from remediator.core.rule_engine import BaseRule, RuleBlockedError, apply_command_rules
from remediator.core.rules import default_builtin_rules
from remediator.remediation.knowledge_base import RemediationKnowledgeBase
from remediator.remediation.llm_agent import propose_remediation
from remediator.remediation.loop import RemediationController, build_default_kb_path
from remediator.remediation.models import (
    CommandExecutionOutcome,
    EnvironmentSnapshot,
    LLMRemediationJSON,
    KnowledgeRecord,
    RemediationHistory,
    RemediationSessionResult,
    RemediationTerminationReason,
    StructuredError,
)
from remediator.remediation.parser import assess_fixability, parse_execution_error

logger = logging.getLogger(__name__)


def _exception_from_litellm(e: BaseException) -> bool:
    mod = getattr(type(e), "__module__", "") or ""
    return "litellm" in mod.lower()


def _snapshot_from_environment() -> EnvironmentSnapshot:
    root = False
    try:
        root = os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        root = False
    return EnvironmentSnapshot(
        os_name=platform.system() or "",
        os_version=platform.release() or "",
        shell=os.environ.get("SHELL") or os.environ.get("COMSPEC", ""),
        current_user=os.environ.get("USER") or os.environ.get("USERNAME", ""),
        is_root_or_sudo=root,
        cwd=os.getcwd(),
    )


def _default_kb() -> RemediationKnowledgeBase:
    path = os.environ.get("REMEDIATION_KB_PATH", "").strip()
    if path:
        return RemediationKnowledgeBase(Path(path))
    return RemediationKnowledgeBase(build_default_kb_path())


def _to_outcome(er: ExecutorResult, cmd: str) -> CommandExecutionOutcome:
    return CommandExecutionOutcome(
        command=cmd,
        stdout=er.stdout,
        stderr=er.stderr,
        return_code=er.return_code,
        confidence_score=0.5,
    )


def _session_to_outcome(session: RemediationSessionResult, command: str) -> CommandExecutionOutcome:
    if session.confidence_score is not None:
        cc = float(session.confidence_score)
    elif session.termination == RemediationTerminationReason.SUCCESS:
        cc = 1.0
    else:
        cc = 0.5
    payload = {
        "termination": session.termination.value,
        "message": session.message,
        "final_command": session.final_command,
        "knowledge_saved": session.knowledge_saved,
        "history_chain": session.history.format_arrow_chain(),
        "confidence_score": session.confidence_score if session.confidence_score is not None else cc,
    }
    rc = 0
    if session.termination != RemediationTerminationReason.SUCCESS:
        rc = 1
    if session.termination in (
        RemediationTerminationReason.USER_ABORT,
        RemediationTerminationReason.NOT_FIXABLE,
        RemediationTerminationReason.LLM_FAILURE,
    ):
        rc = max(rc, 2)
    if session.termination == RemediationTerminationReason.LOW_CONFIDENCE:
        rc = max(rc, 4)
    return CommandExecutionOutcome(
        command=command,
        stdout=json.dumps(payload, ensure_ascii=False),
        stderr=(session.final_command or "")[:8000],
        return_code=rc,
        confidence_score=cc,
    )


def _proposal_from_kb(hit: KnowledgeRecord) -> LLMRemediationJSON:
    rw = "来自本地知识库，执行前请确认目标环境一致。"
    return LLMRemediationJSON(
        root_cause=hit.root_cause or "知识库命中案例",
        fixed_command=hit.fixed_command,
        risk_warning=rw,
        requires_precheck_script=False,
        notes="kb_hit",
        confidence_score=confidence_for_remediation_source("kb", hit.fixed_command, rw),
    )


def _emit_metric(collector: Optional[MetricsCollector], m: RemediationMetrics) -> None:
    MetricsCollector.safe_append(collector, m)


def _maybe_write_diagnostic(
    *,
    enabled: bool,
    reports_dir: Optional[Path],
    path_sink: Optional[list],
    bundle: DiagnosticBundle,
) -> None:
    if not enabled:
        return
    try:
        p = write_diagnostic_report(bundle, reports_dir=reports_dir)
        if path_sink is not None and p is not None:
            path_sink.append(p)
    except Exception as e:  # pragma: no cover
        logger.warning("诊断报告生成异常（已忽略）: %s", e)


def _lite_error_summary(structured: StructuredError) -> str:
    return f"{structured.error_category.value}: {(structured.reason or '')[:400]}".strip()


def _outcome_rule_blocked(command: str, rule_name: str, reason: str) -> CommandExecutionOutcome:
    """规则插件阻断执行时的统一返回。"""
    return CommandExecutionOutcome(
        command=command,
        stdout=json.dumps(
            {"rule_blocked": True, "rule": rule_name, "reason": reason},
            ensure_ascii=False,
        ),
        stderr="RULE_BLOCKED",
        return_code=126,
        confidence_score=0.0,
    )


def _metrics_from_dry_report(
    report: Dict[str, Any],
    session_id: str,
    command: str,
    duration_ms: int,
) -> RemediationMetrics:
    status = report.get("status")
    if status == "success":
        ec = "none"
    else:
        st = report.get("structured") or {}
        ec = str(st.get("error_category") or "unknown")
    src = str(report.get("proposal_source") or "none")
    kb_hit = src == "knowledge_base"
    llm_calls = 1 if src == "llm" else 0
    success = status in ("success", "analyzed")
    if status in ("error", "analysis_partial", "skipped_probe"):
        success = False
    return RemediationMetrics(
        session_id=session_id,
        original_command=command,
        kb_hit=kb_hit,
        llm_calls=llm_calls,
        retries=0,
        success=success,
        risk_blocked=False,
        error_category=ec,
        duration_ms=duration_ms,
        dry_run=True,
        termination="dry_run",
        fix_type="",
    )


def analyze_only(
    command: str,
    *,
    backend: Optional[ExecutorBackend] = None,
    knowledge_base: Optional[RemediationKnowledgeBase] = None,
    env: Optional[EnvironmentSnapshot] = None,
    execute_probe: bool = True,
    llm_model: str = "gpt-4o-mini",
    litellm_api_key: Optional[str] = None,
    litellm_api_base: Optional[str] = None,
) -> Dict[str, Any]:
    """
    等价于「仅分析」：不调用 RemediationController.run，因此不会执行任何**修正**命令。

    - backend：probe 使用的执行后端；默认 ``LocalSubprocessBackend()``。
    - execute_probe=True（默认）：对原始 command 执行一次真实 probe 以采集 stderr（符合多数「预检」场景）。
    - execute_probe=False：完全不调用 os/subprocess；仅返回提示（无法得到真实 stderr）。
    """
    kb = knowledge_base or _default_kb()
    env = env or _snapshot_from_environment()
    exec_backend = backend or LocalSubprocessBackend()

    if not execute_probe:
        return {
            "status": "skipped_probe",
            "message": "execute_probe=False：未执行任何命令；无法生成基于 stderr 的结构化分析。",
            "command": command,
            "risk_level_initial": infer_risk_level(command, "").value,
        }

    try:
        first_er = exec_backend.run(command)
    except Exception as e:
        logger.exception("probe 执行失败: %s", e)
        return {"status": "error", "message": str(e), "command": command}

    if first_er.return_code == 0:
        return {
            "status": "success",
            "message": "命令已成功，无需修复建议。",
            "stdout": first_er.stdout,
            "stderr": first_er.stderr,
            "risk_level_initial": infer_risk_level(command, "").value,
        }

    structured = parse_execution_error(
        command=command,
        return_code=first_er.return_code,
        stdout=first_er.stdout,
        stderr=first_er.stderr,
    )
    fixable, why = assess_fixability(structured)
    history = RemediationHistory()

    proposal: Optional[LLMRemediationJSON] = None
    source = "none"

    try:
        hit = kb.query_best_match(structured, env)
        if hit:
            proposal = _proposal_from_kb(hit)
            source = "knowledge_base"
        elif fixable:
            proposal = propose_remediation(
                structured,
                history,
                env,
                model=llm_model,
                api_key=litellm_api_key,
                api_base=litellm_api_base,
                knowledge_base=kb,
            )
            source = "llm"
    except Exception as e:
        logger.exception("分析阶段 LLM/KB 失败: %s", e)
        return {
            "status": "analysis_partial",
            "fixable": fixable,
            "fixability_reason": why,
            "structured": structured.model_dump(),
            "error": str(e),
        }

    risk_fix = (
        infer_risk_level(proposal.fixed_command, proposal.risk_warning or "")
        if proposal
        else RiskLevel.LOW
    )

    return {
        "status": "analyzed",
        "fixable": fixable,
        "fixability_reason": why,
        "structured": structured.model_dump(),
        "proposal": proposal.model_dump() if proposal else None,
        "proposal_source": source,
        "risk_level_initial": infer_risk_level(command, "").value,
        "risk_level_fix": risk_fix.value,
        "probe": {
            "return_code": first_er.return_code,
            "stdout": first_er.stdout,
            "stderr": first_er.stderr,
        },
    }


def run_with_remediation(
    command: str,
    *,
    backend: Optional[ExecutorBackend] = None,
    dry_run: bool = False,
    interactive: bool = True,
    max_retries: int = 3,
    dry_run_execute_probe: bool = True,
    confirm_high_risk: bool = False,
    knowledge_base: Optional[RemediationKnowledgeBase] = None,
    env: Optional[EnvironmentSnapshot] = None,
    llm_model: str = "gpt-4o-mini",
    litellm_api_key: Optional[str] = None,
    litellm_api_base: Optional[str] = None,
    record_metrics: bool = True,
    metrics_collector: Optional[MetricsCollector] = None,
    write_diagnostic_reports: bool = True,
    reports_directory: Optional[Path] = None,
    diagnostic_report_path: Optional[list] = None,
    confidence_execute_threshold: float = 0.4,
    rules: Optional[Sequence[BaseRule]] = None,
) -> CommandExecutionOutcome:
    """
    包装 core.executor + remediation RemediationController。

    backend：命令执行后端；默认 ``LocalSubprocessBackend()``（与本机 ``run_command`` 行为一致）。

    dry_run=True：不调用 Controller.run；仅执行 analyze_only（默认含一次 probe），结果 JSON 写入 stdout。
    confirm_high_risk=False：若初始命令判定为 HIGH，则直接拒绝进入自愈闭环。

    write_diagnostic_reports：是否在 ``reports/{session_id}.md`` 写入 Markdown 诊断报告。
    diagnostic_report_path：若传入 ``list``，成功写入时将 ``Path`` append 到列表（供 CLI ``--explain``）。

    confidence_execute_threshold：修复置信度低于该阈值时，阻止自动执行修正命令（Lite 路径与 Controller 内提案均生效；等同 DRY_RUN + 告警）。若设置环境变量 ``REMEDIATION_CONFIDENCE_THRESHOLD`` 则优先生效。

    rules：可插拔规则链；默认使用 ``default_builtin_rules()``。执行前以 ``error=None`` 做预处理，解析出 :class:`StructuredError` 后对修正命令做后处理（如 OOM 场景追加资源参数）。
    """
    t0 = time.monotonic()
    session_id = str(uuid.uuid4())
    collector: Optional[MetricsCollector] = None
    if record_metrics and os.environ.get("REMEDIATION_METRICS_DISABLE", "").strip() not in (
        "1",
        "true",
        "yes",
    ):
        collector = metrics_collector or MetricsCollector()

    exec_backend = backend or LocalSubprocessBackend()
    kb = knowledge_base or _default_kb()
    env = env or _snapshot_from_environment()
    ce_thr = float(confidence_execute_threshold)
    _env_ct = os.environ.get("REMEDIATION_CONFIDENCE_THRESHOLD", "").strip()
    if _env_ct:
        ce_thr = float(_env_ct)

    def duration_ms() -> int:
        return int((time.monotonic() - t0) * 1000)

    rules_list: List[BaseRule] = list(rules) if rules is not None else default_builtin_rules()
    submitted_command = command
    try:
        command = apply_command_rules(rules_list, command, None)
    except RuleBlockedError as e:
        logger.warning("规则阻断初始命令: %s", e.reason)
        out = _outcome_rule_blocked(submitted_command, e.rule_name, e.reason)
        _emit_metric(
            collector,
            RemediationMetrics(
                session_id=session_id,
                original_command=submitted_command,
                kb_hit=False,
                llm_calls=0,
                retries=0,
                success=False,
                risk_blocked=False,
                error_category="rule_blocked",
                duration_ms=duration_ms(),
                dry_run=False,
                termination="rule_blocked",
                fix_type="",
            ),
        )
        _maybe_write_diagnostic(
            enabled=write_diagnostic_reports,
            reports_dir=reports_directory,
            path_sink=diagnostic_report_path,
            bundle=DiagnosticBundle(
                session_id=session_id,
                original_command=submitted_command,
                duration_ms=duration_ms(),
                outcome="Blocked",
                env=env,
                termination="rule_blocked",
                blocked_detail=str(e.reason),
                history_arrow_chain=submitted_command,
            ),
        )
        return out

    if not confirm_high_risk and infer_risk_level(command, "") == RiskLevel.HIGH:
        logger.warning("已拦截 HIGH 风险初始命令（confirm_high_risk=False）")
        out = CommandExecutionOutcome(
            command=submitted_command,
            stdout=json.dumps(
                {
                    "blocked": True,
                    "reason": "HIGH risk initial command; set confirm_high_risk=True to allow.",
                    "risk_level": RiskLevel.HIGH.value,
                },
                ensure_ascii=False,
            ),
            stderr="POLICY_BLOCK_HIGH_INITIAL",
            return_code=126,
            confidence_score=0.0,
        )
        _emit_metric(
            collector,
            RemediationMetrics(
                session_id=session_id,
                original_command=submitted_command,
                kb_hit=False,
                llm_calls=0,
                retries=0,
                success=False,
                risk_blocked=True,
                error_category="policy_high_initial",
                duration_ms=duration_ms(),
                dry_run=False,
                termination="blocked",
                fix_type="",
            ),
        )
        _maybe_write_diagnostic(
            enabled=write_diagnostic_reports,
            reports_dir=reports_directory,
            path_sink=diagnostic_report_path,
            bundle=DiagnosticBundle(
                session_id=session_id,
                original_command=submitted_command,
                duration_ms=duration_ms(),
                outcome="Blocked",
                env=env,
                termination="blocked",
                blocked_detail=(
                    "初始命令被判定为 **HIGH** 风险且未设置 `confirm_high_risk=True`，已拦截，未执行。"
                ),
                history_arrow_chain=submitted_command,
            ),
        )
        return out

    if dry_run:
        try:
            report = analyze_only(
                command,
                backend=exec_backend,
                knowledge_base=kb,
                env=env,
                execute_probe=dry_run_execute_probe,
                llm_model=llm_model,
                litellm_api_key=litellm_api_key,
                litellm_api_base=litellm_api_base,
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _exception_from_litellm(e):
                logger.exception("LiteLLM 异常: %s", e)
            else:
                logger.exception("dry-run 分析失败: %s", e)
            out = CommandExecutionOutcome(
                command=submitted_command,
                stdout="",
                stderr=str(e),
                return_code=3,
            )
            _emit_metric(
                collector,
                RemediationMetrics(
                    session_id=session_id,
                    original_command=submitted_command,
                    kb_hit=False,
                    llm_calls=0,
                    retries=0,
                    success=False,
                    risk_blocked=False,
                    error_category="error",
                    duration_ms=duration_ms(),
                    dry_run=True,
                    termination="dry_run_error",
                    fix_type="",
                ),
            )
            _maybe_write_diagnostic(
                enabled=write_diagnostic_reports,
                reports_dir=reports_directory,
                path_sink=diagnostic_report_path,
                bundle=DiagnosticBundle(
                    session_id=session_id,
                    original_command=submitted_command,
                    duration_ms=duration_ms(),
                    outcome="Failed",
                    env=env,
                    termination="dry_run_error",
                    dry_run_report={"status": "error", "message": str(e)},
                    session_message="dry-run 分析抛错（见 stderr）",
                    extra_lines=[f"异常类型: {type(e).__name__}", f"异常信息: {e}"],
                ),
            )
            return out
        out = CommandExecutionOutcome(
            command=submitted_command,
            stdout=json.dumps(report, ensure_ascii=False, indent=2),
            stderr="DRY_RUN_ANALYSIS",
            return_code=0 if report.get("status") != "error" else 3,
        )
        _emit_metric(collector, _metrics_from_dry_report(report, session_id, submitted_command, duration_ms()))
        dr_status = str(report.get("status") or "")
        if dr_status in ("error", "analysis_partial", "skipped_probe"):
            dr_outcome = "Failed"
        elif dr_status in ("success", "analyzed"):
            dr_outcome = "Success"
        else:
            dr_outcome = "Failed"
        prop = report.get("proposal") or {}
        root_txt = None
        if isinstance(prop, dict):
            root_txt = prop.get("root_cause") or None
        _maybe_write_diagnostic(
            enabled=write_diagnostic_reports,
            reports_dir=reports_directory,
            path_sink=diagnostic_report_path,
            bundle=DiagnosticBundle(
                session_id=session_id,
                original_command=submitted_command,
                duration_ms=duration_ms(),
                outcome=dr_outcome,
                env=env,
                termination="dry_run",
                dry_run_report=report,
                root_cause_text=root_txt,
                kb_hit=str(report.get("proposal_source") or "") == "knowledge_base",
                llm_calls=1 if str(report.get("proposal_source") or "") == "llm" else 0,
                session_message=str(report.get("message") or ""),
            ),
        )
        return out

    risk_fix_blocked = [False]

    first_er = exec_backend.run(command)
    if first_er.return_code == 0:
        hist0 = RemediationHistory()
        hist0.append("original_command", submitted_command)
        session_ok = RemediationSessionResult(
            termination=RemediationTerminationReason.SUCCESS,
            message="命令已成功执行，无需修正。",
            history=hist0,
        )
        out0 = _session_to_outcome(session_ok, submitted_command)
        _emit_metric(
            collector,
            RemediationMetrics(
                session_id=session_id,
                original_command=submitted_command,
                kb_hit=False,
                llm_calls=0,
                retries=0,
                success=True,
                risk_blocked=False,
                error_category="none",
                duration_ms=duration_ms(),
                dry_run=False,
                termination="success",
                fix_type="",
            ),
        )
        _maybe_write_diagnostic(
            enabled=write_diagnostic_reports,
            reports_dir=reports_directory,
            path_sink=diagnostic_report_path,
            bundle=DiagnosticBundle(
                session_id=session_id,
                original_command=submitted_command,
                duration_ms=duration_ms(),
                outcome="Success",
                env=env,
                termination="success",
                history_arrow_chain=hist0.format_arrow_chain(),
                session_message=session_ok.message,
            ),
        )
        return out0

    structured0 = parse_execution_error(
        command=command,
        return_code=first_er.return_code,
        stdout=first_er.stdout,
        stderr=first_er.stderr,
    )

    def guarded_execute(cmd: str) -> CommandExecutionOutcome:
        try:
            cmd2 = apply_command_rules(rules_list, cmd, structured0)
        except RuleBlockedError as e:
            logger.warning("规则阻断修正命令: %s", e.reason)
            return _outcome_rule_blocked(cmd, e.rule_name, e.reason)
        lvl = infer_risk_level(cmd2, "")
        if lvl == RiskLevel.HIGH and not confirm_high_risk:
            risk_fix_blocked[0] = True
            logger.warning("拦截 HIGH 风险修正命令: %s", cmd2[:120])
            return CommandExecutionOutcome(
                command=cmd2,
                stdout="",
                stderr="POLICY_BLOCK_HIGH_FIX",
                return_code=126,
            )
        try:
            er = exec_backend.run(cmd2)
            return _to_outcome(er, cmd2)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.exception("executor backend.run 失败: %s", e)
            return CommandExecutionOutcome(
                command=cmd2,
                stdout="",
                stderr=str(e),
                return_code=1,
            )

    lite_cmd = try_lite_fix(structured0, env=env)
    if lite_cmd:
        lite_conf = confidence_for_remediation_source("lite", lite_cmd, "")
        if lite_conf < ce_thr:
            logger.warning(
                "Lite Fix 置信度 %.3f 低于阈值 %.3f，阻止自动执行（等同 DRY_RUN）",
                lite_conf,
                ce_thr,
            )
            out_lc = CommandExecutionOutcome(
                command=submitted_command,
                stdout=json.dumps(
                    {
                        "low_confidence": True,
                        "confidence_score": lite_conf,
                        "threshold": ce_thr,
                        "proposed_command": lite_cmd,
                        "reason": (
                            "修复置信度低于阈值，已阻止自动执行轻量修正；请人工审核后再执行。"
                        ),
                        "structured_initial": structured0.model_dump(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                stderr="CONFIDENCE_TOO_LOW",
                return_code=4,
                confidence_score=lite_conf,
            )
            _emit_metric(
                collector,
                RemediationMetrics(
                    session_id=session_id,
                    original_command=submitted_command,
                    kb_hit=False,
                    llm_calls=0,
                    retries=0,
                    success=False,
                    risk_blocked=False,
                    error_category=structured0.error_category.value,
                    duration_ms=duration_ms(),
                    dry_run=False,
                    termination="low_confidence_lite",
                    fix_type="lite",
                ),
            )
            _maybe_write_diagnostic(
                enabled=write_diagnostic_reports,
                reports_dir=reports_directory,
                path_sink=diagnostic_report_path,
                bundle=DiagnosticBundle(
                    session_id=session_id,
                    original_command=submitted_command,
                    duration_ms=duration_ms(),
                    outcome="Failed",
                    env=env,
                    termination="low_confidence_lite",
                    structured_initial=structured0,
                    session_message="Lite Fix 置信度不足，已阻止执行",
                    extra_lines=[
                        f"proposed_command: {lite_cmd}",
                        f"confidence={lite_conf:.4f}, threshold={ce_thr}",
                    ],
                ),
            )
            return out_lc
        out_lite = guarded_execute(lite_cmd)
        if out_lite.return_code == 0:
            hist_l = RemediationHistory()
            hist_l.append("original_command", submitted_command)
            hist_l.append("error", _lite_error_summary(structured0))
            hist_l.append("fix_command", out_lite.command)
            session_lite = RemediationSessionResult(
                termination=RemediationTerminationReason.SUCCESS,
                message="轻量级自动修正成功。",
                history=hist_l,
                final_command=out_lite.command,
                knowledge_saved=False,
                confidence_score=lite_conf,
            )
            out_l = _session_to_outcome(session_lite, submitted_command)
            _emit_metric(
                collector,
                RemediationMetrics(
                    session_id=session_id,
                    original_command=submitted_command,
                    kb_hit=False,
                    llm_calls=0,
                    retries=1,
                    success=True,
                    risk_blocked=risk_fix_blocked[0],
                    error_category=structured0.error_category.value,
                    duration_ms=duration_ms(),
                    dry_run=False,
                    termination="success",
                    fix_type="lite",
                ),
            )
            _maybe_write_diagnostic(
                enabled=write_diagnostic_reports,
                reports_dir=reports_directory,
                path_sink=diagnostic_report_path,
                bundle=DiagnosticBundle(
                    session_id=session_id,
                    original_command=submitted_command,
                    duration_ms=duration_ms(),
                    outcome="Success",
                    env=env,
                    termination="success",
                    structured_initial=structured0,
                    root_cause_text=(
                        "轻量级规则（Lite Fix）命中：基于 StructuredError 类别生成的单行修正命令，未经过 KB/LLM。"
                    ),
                    history_arrow_chain=hist_l.format_arrow_chain(),
                    kb_hit=False,
                    llm_calls=0,
                    fix_type="lite",
                    session_message=session_lite.message,
                ),
            )
            return out_l

    llm_calls = [0]
    kb_hit_flag = [False]
    first_category: list[Optional[str]] = [structured0.error_category.value]

    _orig_llm = loop_mod.propose_remediation
    _orig_parse = loop_mod.parse_execution_error
    _orig_kb_q = kb.query_best_match

    def _wrap_llm(*args: Any, **kwargs: Any) -> Any:
        llm_calls[0] += 1
        return _orig_llm(*args, **kwargs)

    def _wrap_parse(*args: Any, **kwargs: Any) -> Any:
        se = _orig_parse(*args, **kwargs)
        if first_category[0] is None:
            first_category[0] = se.error_category.value
        return se

    def _wrap_kb(self: RemediationKnowledgeBase, error: Any, env: EnvironmentSnapshot) -> Any:
        r = _orig_kb_q(error, env)
        if r is not None:
            kb_hit_flag[0] = True
        return r

    loop_mod.propose_remediation = _wrap_llm
    loop_mod.parse_execution_error = _wrap_parse
    kb.query_best_match = types.MethodType(_wrap_kb, kb)

    controller = RemediationController(
        execute_fn=guarded_execute,
        knowledge_base=kb,
        env=env,
        interactive=interactive,
        max_retries=max_retries,
        llm_model=llm_model,
        litellm_api_key=litellm_api_key,
        litellm_api_base=litellm_api_base,
        confidence_execute_threshold=ce_thr,
    )

    session: Optional[RemediationSessionResult] = None
    exception_out: Optional[CommandExecutionOutcome] = None
    try:
        session = controller.run(command)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        if _exception_from_litellm(e):
            logger.exception("LiteLLM 异常: %s", e)
        else:
            logger.exception("controller.run 失败: %s", e)
        exception_out = CommandExecutionOutcome(
            command=submitted_command,
            stdout="",
            stderr=str(e),
            return_code=3,
        )
    finally:
        loop_mod.propose_remediation = _orig_llm
        loop_mod.parse_execution_error = _orig_parse
        kb.query_best_match = _orig_kb_q

    if exception_out is not None:
        _emit_metric(
            collector,
            RemediationMetrics(
                session_id=session_id,
                original_command=submitted_command,
                kb_hit=kb_hit_flag[0],
                llm_calls=llm_calls[0],
                retries=0,
                success=False,
                risk_blocked=risk_fix_blocked[0],
                error_category=first_category[0] or "exception",
                duration_ms=duration_ms(),
                dry_run=False,
                termination="exception",
                fix_type="",
            ),
        )
        _maybe_write_diagnostic(
            enabled=write_diagnostic_reports,
            reports_dir=reports_directory,
            path_sink=diagnostic_report_path,
            bundle=DiagnosticBundle(
                session_id=session_id,
                original_command=submitted_command,
                duration_ms=duration_ms(),
                outcome="Failed",
                env=env,
                termination="exception",
                structured_initial=structured0,
                kb_hit=kb_hit_flag[0],
                llm_calls=llm_calls[0],
                session_message="RemediationController.run 或闭环阶段抛异常",
                extra_lines=[f"stderr/异常摘要: {exception_out.stderr[:2000] if exception_out else ''}"],
            ),
        )
        return exception_out

    assert session is not None
    retries = count_fix_retries(session.history)
    success = session.termination == RemediationTerminationReason.SUCCESS
    risk_blocked = risk_fix_blocked[0]

    out = _session_to_outcome(session, submitted_command)

    _emit_metric(
        collector,
        RemediationMetrics(
            session_id=session_id,
            original_command=submitted_command,
            kb_hit=kb_hit_flag[0],
            llm_calls=llm_calls[0],
            retries=retries,
            success=success,
            risk_blocked=risk_blocked,
            error_category=first_category[0] or "unknown",
            duration_ms=duration_ms(),
            dry_run=False,
            termination=session.termination.value,
            fix_type="",
        ),
    )
    _maybe_write_diagnostic(
        enabled=write_diagnostic_reports,
        reports_dir=reports_directory,
        path_sink=diagnostic_report_path,
        bundle=DiagnosticBundle(
            session_id=session_id,
            original_command=submitted_command,
            duration_ms=duration_ms(),
            outcome=("Success" if success else "Failed"),
            env=env,
            termination=session.termination.value,
            structured_initial=structured0,
            root_cause_text=session.message or None,
            history_arrow_chain=session.history.format_arrow_chain(),
            kb_hit=kb_hit_flag[0],
            llm_calls=llm_calls[0],
            fix_type="",
            session_message=session.message,
        ),
    )
    return out


__all__ = [
    "RiskLevel",
    "infer_risk_level",
    "analyze_only",
    "run_with_remediation",
    "DockerExecBackend",
    "ExecutorBackend",
    "LocalSubprocessBackend",
]
