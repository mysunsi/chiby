"""Phase 3：执行 → 缓存镜像 → 成败判定（exit / LLM / 两者）→ 成功归档；失败则 LLM 修复 + 网关 + 重试（≤3 轮）。"""
from __future__ import annotations

import re
import shlex
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from chibycore.closure_llm_fix import filter_fix_commands_for_shell
from chibycore.closure_llm_judge import llm_judge_closure_outcome
from chibycore.closure_service import (
    RetryBudget,
    build_closure_payload,
    success_for_closure,
)
from chibycore.execution_gateway import GatewayAllowResult, gateway_allow_detail
from chibycore.executor_contract import ClosurePayload, ExecResult
from chibycore.kb_closure_archive import archive_closure_success

# 只读探测：有合法诊断输出即可，勿因 systemctl status 退出码 3（未运行）触发自愈
_DIAGNOSTIC_CMD_RE = re.compile(
    r"(?i)^\s*(?:sudo\s+)?"
    r"(?:"
    r"systemctl\s+(?:status|is-active|is-enabled|is-failed|show|list-units|"
    r"list-unit-files|cat)\b|"
    r"(?:df|free|uptime|hostname|uname|whoami|nproc|swapon)\b|"
    r"(?:ps|ss|ip|ifconfig)\s|"
    r"Get-(?:Service|Process|PSDrive|CimInstance|ComputerInfo|NetIPAddress|Volume)\b"
    r")",
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{2,}")
_TOKEN_NOISE = frozenset(
    {
        "sudo", "the", "and", "not", "for", "path", "force", "error", "action",
        "item", "file", "does", "exist", "nothing", "remove", "write", "output",
        "test", "else", "then", "true", "false", "null", "with", "from", "this",
        "that", "have", "been", "will", "command", "status",
    }
)

# 知识库检索（懒加载，避免 sqalchemy 在模块级别加载）
_KB_SEARCH: Optional["KnowledgeHubSearch"] = None  # noqa: F821

def _get_kb_search():
    global _KB_SEARCH
    if _KB_SEARCH is None:
        from chibycore.knowledge_hub.search import KnowledgeHubSearch
        from chibycore.knowledge_hub.models import SearchQuery
        _KB_SEARCH = KnowledgeHubSearch()
    return _KB_SEARCH


# 兼容旧回调 Tuple[bool, str]；推荐返回 GatewayAllowResult
GatewayAllowFn = Callable[[str], Union[GatewayAllowResult, Tuple[bool, str]]]
ExecuteFn = Callable[[str], ExecResult]
FixCommandsFn = Callable[[List[ClosurePayload]], List[str]]
OnSuccessFn = Callable[[ClosurePayload], None]
AfterExecuteFn = Callable[[ClosurePayload], None]
AfterStepFn = Callable[["ClosureStepRecord"], None]
JudgeFn = Callable[[ClosurePayload], Tuple[bool, str]]


def _coerce_gateway_allow(raw: Any) -> GatewayAllowResult:
    """将 gateway_allow 回调结果统一为 GatewayAllowResult。"""
    if isinstance(raw, GatewayAllowResult):
        return raw
    if isinstance(raw, tuple) and len(raw) >= 2:
        return GatewayAllowResult(
            allowed=bool(raw[0]),
            reason=str(raw[1] or ""),
            pending_change_control=bool(raw[2]) if len(raw) > 2 else False,
            pending_id=str(raw[3] or "") if len(raw) > 3 else "",
        )
    # ExecutionOutcome 或带同名字段的对象
    if hasattr(raw, "allowed"):
        return GatewayAllowResult(
            allowed=bool(raw.allowed),
            reason=str(getattr(raw, "reason", "") or ""),
            pending_change_control=bool(
                getattr(raw, "pending_change_control", False)
            ),
            pending_id=str(getattr(raw, "pending_id", "") or ""),
            denial_category=str(getattr(raw, "denial_category", "") or ""),
            rule_kind=str(getattr(raw, "rule_kind", "") or ""),
            matched_pattern=str(getattr(raw, "matched_pattern", "") or ""),
            override_requires_approval=bool(
                getattr(raw, "override_requires_approval", False)
            ),
            progressive_policy_hint=str(
                getattr(raw, "progressive_policy_hint", "") or ""
            ),
        )
    raise TypeError(
        f"gateway_allow 应返回 GatewayAllowResult 或 (allowed, reason[, ...])，得到 {type(raw)!r}"
    )


def _step_from_gateway(
    *,
    phase: str,
    command: str,
    g: GatewayAllowResult,
    fix_round: int = 0,
    result: Optional[ExecResult] = None,
    payload: Optional[ClosurePayload] = None,
) -> ClosureStepRecord:
    return ClosureStepRecord(
        phase=phase,
        command=command,
        gateway_allowed=bool(g.allowed),
        gateway_reason=(g.reason or "") if not g.allowed else "",
        result=result,
        payload=payload,
        fix_round=fix_round,
        pending_change_control=bool(g.pending_change_control),
        change_control_pending_id=(g.pending_id or "") if g.pending_change_control else "",
        gateway_detail=gateway_allow_detail(g),
    )


@dataclass
class ClosureStepRecord:
    phase: str  # initial | fix | goal_resume
    command: str
    gateway_allowed: bool
    gateway_reason: str
    result: Optional[ExecResult] = None
    payload: Optional[ClosurePayload] = None
    fix_round: int = 0
    exit_ok: Optional[bool] = None
    llm_judge_ok: Optional[bool] = None
    llm_judge_reason: str = ""
    outcome_detail: str = ""
    #: 命中变更冻结窗口（待审批，非策略拒绝）
    pending_change_control: bool = False
    change_control_pending_id: str = ""
    #: 网关拒绝时可解释性载荷（denial_category / rule_kind 等）
    gateway_detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClosureRunResult:
    ok: bool
    final_payload: Optional[ClosurePayload]
    steps: List[ClosureStepRecord] = field(default_factory=list)
    stop_reason: str = ""
    # 本轮实际用过的修复来源（knowledge_hub / remediator / llm / heuristic / custom）
    fix_sources: List[str] = field(default_factory=list)


_PERM_RE = re.compile(
    r"(?i)permission\s+denied|operation\s+not\s+permitted|"
    r"not\s+permitted|must\s+be\s+root|requires?\s+root|"
    r"超级用户|权限不够|权限不足"
)


def _norm_cmd(s: str) -> str:
    return " ".join(str(s or "").strip().split())


def _strip_leading_sudo(cmd: str) -> str:
    s = _norm_cmd(cmd)
    s = re.sub(r"(?i)^sudo\s+(-[nEn]+\s+)*", "", s).strip()
    m = re.match(r"(?i)^(bash|sh)\s+-lc\s+(.*)$", s, re.DOTALL)
    if not m:
        return s
    inner = (m.group(2) or "").strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in ("'", '"'):
        inner = inner[1:-1]
    return inner.strip()


def fix_covers_original_goal(fix_cmd: str, initial_cmd: str) -> bool:
    """修复命令是否已等价覆盖原目标（无需再复验原意图）。"""
    fix_n = _norm_cmd(fix_cmd)
    init_n = _norm_cmd(initial_cmd)
    if not fix_n or not init_n:
        return False
    if fix_n == init_n:
        return True
    if _strip_leading_sudo(fix_n) == _strip_leading_sudo(init_n):
        # 同内容且修复侧带 sudo / bash -lc 提权
        if fix_n.lower().startswith("sudo") or _strip_leading_sudo(fix_n) != fix_n:
            return True
    return False


def _history_suggests_permission(history: List[ClosurePayload]) -> bool:
    for cp in history[:2]:
        blob = "\n".join(
            [
                str(getattr(cp, "stderr", "") or ""),
                str(getattr(cp, "stdout", "") or ""),
            ]
        )
        if _PERM_RE.search(blob):
            return True
    return False


def build_goal_resume_command(
    initial_cmd: str,
    successful_fix: str,
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
) -> Optional[str]:
    """修复命令本身通过后，若尚未覆盖原意图，构造「续跑原目标」命令。"""
    init_n = _norm_cmd(initial_cmd)
    fix_n = _norm_cmd(successful_fix)
    if not init_n or not fix_n:
        return None
    if fix_covers_original_goal(fix_n, init_n):
        return None

    sp = (shell_profile or "unix").strip().lower()
    if sp in ("powershell", "pwsh", "windows", "winrm"):
        # Windows：环境类修复后直接重试原命令
        return init_n if fix_n != init_n else None

    elev = _history_suggests_permission(history) or fix_n.lower().startswith("sudo")
    # 复合命令被缩成短修复（如只跑了 nginx -t）→ 提权后重跑整条原意图
    compound = any(x in init_n for x in ("&&", ";", "|"))
    first_seg = init_n.split("&&")[0].strip() if "&&" in init_n else init_n
    fix_core = _strip_leading_sudo(fix_n)
    subset = False
    if compound and fix_core:
        if fix_core == _strip_leading_sudo(first_seg):
            subset = True
        elif len(fix_core) + 8 < len(init_n) and fix_core in init_n:
            subset = True

    pkg_prereq = bool(
        re.match(
            r"(?i)^(sudo\s+)?(apt(-get)?|yum|dnf|zypper|pacman)\s+(install|add)\b",
            fix_n,
        )
    )

    if elev or subset:
        if init_n.lower().startswith("sudo"):
            return init_n
        if compound or any(c in init_n for c in ('"', "'", "(", ")", "<", ">", "`")):
            return "sudo bash -lc " + shlex.quote(init_n)
        return "sudo " + init_n

    # 装包等前置修复：环境就绪后重试原命令（非整命令替换）
    if pkg_prereq and fix_n != init_n:
        return init_n

    # 其余情况视为「用新命令替换原命令」已达成目标，不再复跑失败原句
    return None


def evaluate_closure_success_detailed(
    cp: ClosurePayload,
    *,
    success_mode: str,
    success_exit_codes: Optional[List[int]],
    llm_judge_fn: Optional[JudgeFn] = None,
) -> Tuple[bool, str, bool, Optional[bool], str]:
    """
    返回：(综合是否成功, detail 文本, exit_ok, llm_judge_ok|None, llm_judge_reason)。
    llm_judge_ok 为 None 表示本轮未咨询 LLM（exit_code 模式）。
    """
    mode = (success_mode or "exit_code").strip().lower()
    exit_ok = success_for_closure(cp, success_exit_codes)
    fn = llm_judge_fn or llm_judge_closure_outcome

    from chibycore.closure_labels import format_both_mode_detail, humanize_judge_reason

    if mode == "exit_code":
        detail = "退出码通过" if exit_ok else "退出码未通过"
        return exit_ok, detail, exit_ok, None, ""

    lj, reason = fn(cp)
    lr = (reason or "")[:800]

    if mode == "llm":
        rsn = (reason or "").strip() or ("智能判定通过" if lj else "智能判定未通过")
        return lj, rsn, exit_ok, lj, lr

    if mode == "both":
        combined = bool(exit_ok and lj)
        detail = format_both_mode_detail(exit_ok=exit_ok, llm_ok=lj, reason=reason)
        return combined, detail, exit_ok, lj, lr

    ok = exit_ok
    return ok, humanize_judge_reason("unknown_mode_fallback_exit_code"), exit_ok, None, ""


def evaluate_closure_success(
    cp: ClosurePayload,
    *,
    success_mode: str,
    success_exit_codes: Optional[List[int]],
    llm_judge_fn: Optional[JudgeFn] = None,
) -> Tuple[bool, str]:
    """
    success_mode:
      - exit_code：仅 exit_code ∈ success_exit_codes（默认 [0]）
      - llm：仅 LLM 判定（不可用时回退 exit_code）
      - both：exit 通过 且 LLM 判定成功
    """
    ok, detail, _, _, _ = evaluate_closure_success_detailed(
        cp,
        success_mode=success_mode,
        success_exit_codes=success_exit_codes,
        llm_judge_fn=llm_judge_fn,
    )
    return ok, detail


def is_readonly_diagnostic_command(command: str) -> bool:
    """只读状态/资源探测：不应因非零退出码（如 systemctl status=3）进入自愈。"""
    return bool(_DIAGNOSTIC_CMD_RE.match((command or "").strip()))


def _significant_tokens(text: str) -> set:
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if t.lower() not in _TOKEN_NOISE
    }


def kb_fix_relevant_to_command(original_cmd: str, fix_cmd: str) -> bool:
    """知识库修复须与失败命令有词面重叠，避免把删 a.dat 套到 nginx status。"""
    o = _significant_tokens(original_cmd)
    if not o:
        return True
    f = _significant_tokens(fix_cmd)
    return bool(o & f)


def diagnostic_closure_ok(command: str, cp: ClosurePayload) -> bool:
    """只读探测拿到可解读结果即视为闭环成功（含 unit inactive / not-found）。"""
    if not is_readonly_diagnostic_command(command):
        return False
    try:
        code = int(cp.exit_code)
    except (TypeError, ValueError):
        return False
    blob = f"{cp.stdout or ''}{cp.stderr or ''}"
    if re.search(r"(?i)syntax error|unexpected token", blob):
        return False
    # systemctl：0–4 均为合法诊断；其它只读：有输出且非壳语法错即可
    if re.search(r"(?i)\bsystemctl\b", command or ""):
        return 0 <= code <= 4
    if re.search(r"(?i)\bGet-Service\b|\bGet-Process\b", command or ""):
        return code == 0 or bool(blob.strip())
    return code == 0 or bool(blob.strip())


def _lookup_kb_fixes(
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
) -> Optional[List[str]]:
    """查询 KnowledgeHub，若存在高匹配度的历史修复则直接返回，否则返回 None 让调用方退回到 LLM。"""
    if not history:
        return None

    # 从第一条 payload 提取命令 + 错误信息
    first = history[0]
    cmd = first.effective_command or first.raw_command or ""
    stderr = (first.stderr or "")[:1000]
    stdout = (first.stdout or "")[:1000]
    query_text = f"{cmd} {stderr} {stdout}".strip()[:500]
    if not query_text:
        return None

    try:
        from chibycore.knowledge_hub.models import SearchQuery

        searcher = _get_kb_search()
        sq = SearchQuery(
            q=query_text,
            mode="kb",
            limit=3,
        )
        resp = searcher.search(sq)
        if not resp.results:
            return None

        # 只取高置信度（score >= 0.3）且是修复类结果
        fixes: List[str] = []
        for r in resp.results:
            if r.score >= 0.3:
                # 从 result title/snippet 无法直接拿到 remediation 命令，
                # 需要通过 entry_id 回查 storage
                try:
                    storage = searcher._storage
                    entry = storage.get_kb_entry(r.entry_id)
                    if entry and entry.remediation and entry.remediation.strip():
                        fixes.append(entry.remediation.strip())
                except Exception:
                    continue

        if not fixes:
            return None

        # 壳/发行版过滤 + 与原命令相关性（防串台）
        fixes = filter_fix_commands_for_shell(
            fixes,
            shell_profile,
            distro_family=distro_family,
            pkg_manager=pkg_manager,
        )
        fixes = [f for f in fixes if kb_fix_relevant_to_command(cmd, f)]
        if fixes:
            logger = __import__("logging").getLogger(__name__)
            logger.info(
                "知识库命中 %d 条可用修复方案: %s",
                len(fixes), [f[:60] for f in fixes],
            )
            return fixes
        logger = __import__("logging").getLogger(__name__)
        logger.info(
            "知识库命中已丢弃（壳不符或与原命令无关）cmd=%s",
            (cmd or "")[:80],
        )
    except Exception as ex:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("知识库查询失败（非致命，退回到 LLM）: %s", ex)

    return None


def run_closure_retry_loop(
    *,
    trace_id: str,
    initial_command: str,
    execute: ExecuteFn,
    gateway_allow: GatewayAllowFn,
    shell_profile: str = "unix",
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
    nl_intent_hint: Optional[str] = None,
    session_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    max_fix_attempts: int = 3,
    success_exit_codes: Optional[List[int]] = None,
    success_mode: str = "exit_code",
    llm_judge_fn: Optional[JudgeFn] = None,
    llm_fix_commands: Optional[FixCommandsFn] = None,
    on_success: Optional[OnSuccessFn] = None,
    archive_kb: bool = False,
    on_after_execute: Optional[AfterExecuteFn] = None,
    on_after_step: Optional[AfterStepFn] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    verify_original_after_fix: bool = True,
) -> ClosureRunResult:
    """
    1) 初始命令先过网关，再 execute → ClosurePayload
    2) on_after_step：每步写入外部（推荐；含网关拒绝与 fix_round）；若未提供则用 on_after_execute(payload)
    3) evaluate_closure_success → 成功则 archive_kb + on_success
    4) 否则最多 max_fix_attempts 轮 LLM 修复（每轮可尝试多条过网关命令）
    5) 修复命令通过后，默认再复验原意图（verify_original_after_fix）：
       避免「只修好 nginx -t」却把「查完整配置」标成成功
    """
    steps: List[ClosureStepRecord] = []
    history: List[ClosurePayload] = []

    def _cancelled() -> bool:
        return bool(cancel_check and cancel_check())

    def _tid(suffix: str = "") -> str:
        return trace_id + suffix

    def _emit_after_step(rec: ClosureStepRecord) -> None:
        if on_after_step:
            on_after_step(rec)
        elif on_after_execute and rec.payload:
            on_after_execute(rec.payload)

    def _fire_success(cp: ClosurePayload, judge_detail: str) -> None:
        if archive_kb:
            archive_closure_success(cp, judge_reason=judge_detail, trace_id=trace_id)
        if on_success:
            on_success(cp)

    g0 = _coerce_gateway_allow(gateway_allow(initial_command.strip()))
    if not g0.allowed:
        steps.append(
            _step_from_gateway(
                phase="initial",
                command=initial_command,
                g=g0,
                fix_round=0,
            )
        )
        if not steps[-1].gateway_reason:
            steps[-1].gateway_reason = "gateway_denied"
        _emit_after_step(steps[-1])
        stop = (
            "initial_change_control_hold"
            if g0.pending_change_control
            else "initial_gateway_denied"
        )
        return ClosureRunResult(
            ok=False,
            final_payload=None,
            steps=steps,
            stop_reason=stop,
        )

    if _cancelled():
        return ClosureRunResult(
            ok=False,
            final_payload=None,
            steps=steps,
            stop_reason="user_cancelled",
        )

    res0 = execute(initial_command)
    cp0 = build_closure_payload(
        trace_id=_tid(""),
        raw_command=initial_command,
        effective_command=initial_command,
        result=res0,
        nl_intent_hint=nl_intent_hint,
        session_id=session_id,
        plan_id=plan_id,
    )
    history.append(cp0)
    steps.append(
        _step_from_gateway(
            phase="initial",
            command=initial_command,
            g=g0,
            fix_round=0,
            result=res0,
            payload=cp0,
        )
    )
    ok0, detail0, ex0, lj0, lr0 = evaluate_closure_success_detailed(
        cp0,
        success_mode=success_mode,
        success_exit_codes=success_exit_codes,
        llm_judge_fn=llm_judge_fn,
    )
    if (not ok0) and diagnostic_closure_ok(initial_command, cp0):
        ok0 = True
        detail0 = "diagnostic_ok"
        ex0 = True
    rec0 = steps[-1]
    rec0.exit_ok = ex0
    rec0.llm_judge_ok = lj0
    rec0.llm_judge_reason = lr0
    rec0.outcome_detail = detail0
    _emit_after_step(rec0)
    if ok0:
        _fire_success(cp0, detail0)
        return ClosureRunResult(True, cp0, steps, "success_initial")

    # 只读探测失败也不进自愈（避免知识库/LLM 串台改写查询）
    if is_readonly_diagnostic_command(initial_command):
        return ClosureRunResult(
            False, cp0, steps, "diagnostic_no_heal",
        )

    budget = RetryBudget(max_attempts=max(0, int(max_fix_attempts)))
    fix_sources: List[str] = []

    def _pick_fixes() -> List[str]:
        # 1) 先查知识库（如果有自定义 llm_fix_commands 则跳过，由外部控制）
        if not llm_fix_commands:
            kb_fixes = _lookup_kb_fixes(
                history,
                shell_profile=shell_profile,
                distro_family=distro_family,
                pkg_manager=pkg_manager,
            )
            if kb_fixes:
                fix_sources.append("knowledge_hub")
                return kb_fixes
        # 2) 自定义修复函数
        if llm_fix_commands:
            fix_sources.append("custom")
            return filter_fix_commands_for_shell(
                llm_fix_commands(history) or [],
                shell_profile,
                distro_family=distro_family,
                pkg_manager=pkg_manager,
            )
        # 3) 默认 LLM / remediator 修复流水线
        from chibycore.closure_llm_fix import call_fix_pipeline_with_source

        fixes, src = call_fix_pipeline_with_source(
            history,
            shell_profile=shell_profile,
            distro_family=distro_family,
            pkg_manager=pkg_manager,
        )
        if fixes and src and src != "none":
            fix_sources.append(src)
        return fixes

    def _result(
        ok: bool,
        payload: Optional[ClosurePayload],
        reason: str,
    ) -> ClosureRunResult:
        # 去重保序
        seen_src: List[str] = []
        for s in fix_sources:
            if s and s not in seen_src:
                seen_src.append(s)
        return ClosureRunResult(
            ok=ok,
            final_payload=payload,
            steps=steps,
            stop_reason=reason,
            fix_sources=seen_src,
        )

    fix_round_counter = 0
    saw_fix_ok_goal_unverified = False
    while budget.can_retry():
        if _cancelled():
            return _result(False, history[-1] if history else None, "user_cancelled")
        budget.consume()
        fix_round_counter += 1
        if _cancelled():
            return _result(False, history[-1] if history else None, "user_cancelled")
        fixes = _pick_fixes()
        # 跳过历史已执行过的候选，避免同一条 PowerShell/错误命令空转多轮
        tried = {
            (cp.effective_command or cp.raw_command or "").strip()
            for cp in history
            if (cp.effective_command or cp.raw_command or "").strip()
        }
        fixes = [c for c in (fixes or []) if (c or "").strip() and (c or "").strip() not in tried]
        if not fixes:
            if saw_fix_ok_goal_unverified:
                return _result(
                    False,
                    history[-1],
                    "repair_ok_goal_unverified",
                )
            return _result(False, history[-1], "no_fix_commands")
        any_allowed_exec = False
        for cmd in fixes:
            if _cancelled():
                return _result(False, history[-1] if history else None, "user_cancelled")
            gf = _coerce_gateway_allow(gateway_allow(cmd))
            if not gf.allowed:
                steps.append(
                    _step_from_gateway(
                        phase="fix",
                        command=cmd,
                        g=gf,
                        fix_round=fix_round_counter,
                    )
                )
                if not steps[-1].gateway_reason:
                    steps[-1].gateway_reason = "denied"
                _emit_after_step(steps[-1])
                continue
            any_allowed_exec = True
            if _cancelled():
                return _result(False, history[-1] if history else None, "user_cancelled")
            r = execute(cmd)
            cp = build_closure_payload(
                trace_id=_tid("_fix_" + uuid.uuid4().hex[:8]),
                raw_command=initial_command,
                effective_command=cmd,
                result=r,
                nl_intent_hint=nl_intent_hint,
                session_id=session_id,
                plan_id=plan_id,
            )
            history.append(cp)
            tried.add((cmd or "").strip())
            steps.append(
                _step_from_gateway(
                    phase="fix",
                    command=cmd,
                    g=gf,
                    fix_round=fix_round_counter,
                    result=r,
                    payload=cp,
                )
            )
            okc, det, ex_ok, lj_ok, lj_rs = evaluate_closure_success_detailed(
                cp,
                success_mode=success_mode,
                success_exit_codes=success_exit_codes,
                llm_judge_fn=llm_judge_fn,
            )
            rec_f = steps[-1]
            rec_f.exit_ok = ex_ok
            rec_f.llm_judge_ok = lj_ok
            rec_f.llm_judge_reason = lj_rs
            rec_f.outcome_detail = det
            _emit_after_step(rec_f)
            if not okc:
                continue

            resume = None
            if verify_original_after_fix:
                resume = build_goal_resume_command(
                    initial_command,
                    cmd,
                    history,
                    shell_profile=shell_profile,
                )
            resume_s = (resume or "").strip()
            if (
                resume_s
                and resume_s != (cmd or "").strip()
                and resume_s not in tried
            ):
                if _cancelled():
                    return _result(False, history[-1] if history else None, "user_cancelled")
                gr = _coerce_gateway_allow(gateway_allow(resume_s))
                if not gr.allowed:
                    steps.append(
                        _step_from_gateway(
                            phase="goal_resume",
                            command=resume_s,
                            g=gr,
                            fix_round=fix_round_counter,
                        )
                    )
                    if not steps[-1].gateway_reason:
                        steps[-1].gateway_reason = "denied"
                    _emit_after_step(steps[-1])
                    saw_fix_ok_goal_unverified = True
                    continue
                rr = execute(resume_s)
                cp_r = build_closure_payload(
                    trace_id=_tid("_resume_" + uuid.uuid4().hex[:8]),
                    raw_command=initial_command,
                    effective_command=resume_s,
                    result=rr,
                    nl_intent_hint=nl_intent_hint,
                    session_id=session_id,
                    plan_id=plan_id,
                )
                history.append(cp_r)
                tried.add(resume_s)
                steps.append(
                    _step_from_gateway(
                        phase="goal_resume",
                        command=resume_s,
                        g=gr,
                        fix_round=fix_round_counter,
                        result=rr,
                        payload=cp_r,
                    )
                )
                ok_r, det_r, ex_r, lj_r, lj_rs_r = evaluate_closure_success_detailed(
                    cp_r,
                    success_mode=success_mode,
                    success_exit_codes=success_exit_codes,
                    llm_judge_fn=llm_judge_fn,
                )
                if (not ok_r) and diagnostic_closure_ok(resume_s, cp_r):
                    ok_r = True
                    det_r = "diagnostic_ok"
                    ex_r = True
                rec_r = steps[-1]
                rec_r.exit_ok = ex_r
                rec_r.llm_judge_ok = lj_r
                rec_r.llm_judge_reason = lj_rs_r
                rec_r.outcome_detail = det_r
                _emit_after_step(rec_r)
                if ok_r:
                    _fire_success(cp_r, det_r)
                    return _result(True, cp_r, "success_after_fix")
                saw_fix_ok_goal_unverified = True
                continue

            _fire_success(cp, det)
            return _result(True, cp, "success_after_fix")
        if not any_allowed_exec:
            return _result(False, history[-1], "all_fix_suggestions_denied_by_gateway")

    if saw_fix_ok_goal_unverified:
        return _result(
            False,
            history[-1] if history else None,
            "repair_ok_goal_unverified",
        )
    return _result(
        False,
        history[-1] if history else None,
        "max_fix_attempts_exhausted",
    )
