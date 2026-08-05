"""桌面 Agent 与掌上AI机房运维模式的共享薄封装。

复用：
- ``LLMPromptProcessor`` / ``try_build_chain_plan`` 规划
- ``run_closure_retry_loop`` 执行 + 检测 + 自愈

避免 mobile 再维护一套独立 Agent 脑。
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, List, Optional, Tuple

from chibycore.closure_retry_runner import ClosureRunResult, run_closure_retry_loop
from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
from chibycore.executor_contract import ExecResult as CoreExecResult
from chibycore.executor_contract import RunOptions
from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host
from chibyterm.chain_bridge import try_build_chain_plan
from chibyterm.llm_shell import LLMPromptProcessor, ShellProfile, command_line_danger

logger = logging.getLogger(__name__)

_processor: Optional[LLMPromptProcessor] = None


def get_prompt_processor() -> LLMPromptProcessor:
    global _processor
    if _processor is None:
        _processor = LLMPromptProcessor()
    return _processor


@dataclass
class AgentPlan:
    ok: bool
    explanation: str = ""
    commands: List[str] = field(default_factory=list)
    source: str = ""  # chain | llm | rule | none
    error: str = ""


def _shell_profile(conn_type: str) -> str:
    ct = (conn_type or "ssh").strip().lower()
    if ct == "winrm":
        return ShellProfile.POWERSHELL.value
    return ShellProfile.UNIX.value


def _session_shim(host_obj: Any, conn_type: str) -> Any:
    """给 try_build_chain_plan 用的最小会话对象。"""
    from chibyterm.models import ConnType

    ct = (conn_type or "ssh").strip().lower()
    enum_ct = ConnType.WINRM if ct == "winrm" else ConnType.SSH
    return SimpleNamespace(
        host=str(getattr(host_obj, "host", "") or "127.0.0.1"),
        username=str(getattr(host_obj, "username", "") or ""),
        password=str(getattr(host_obj, "password", "") or ""),
        conn_type=enum_ct,
    )


def _steps_to_commands(steps: List[dict]) -> List[str]:
    out: List[str] = []
    for s in steps or []:
        cmd = str((s or {}).get("command") or "").strip()
        if cmd and cmd not in out:
            out.append(cmd)
    return out[:12]


_RM_FORCE_RE = re.compile(
    r"^\s*rm\s+-f\s+(?:--\s+)?(.+?)\s*$",
    re.I,
)


def _sanitize_agent_command(cmd: str) -> str:
    """避免 rm -f 在文件不存在时仍 exit 0 造成「假成功」。"""
    s = (cmd or "").strip()
    m = _RM_FORCE_RE.match(s)
    if not m:
        return s
    target = m.group(1).strip()
    if not target or any(x in target for x in (";", "|", "`", "$(", "&&", "||")):
        return s
    return (
        f"test -e {target} && rm -- {target} "
        f'|| {{ echo "文件不存在: {target}" >&2; exit 1; }}'
    )


def plan_agent_nl(
    user_text: str,
    *,
    conn_type: str = "ssh",
    host_obj: Any = None,
) -> AgentPlan:
    """与桌面 Agent 同脑：严格只读意图 → 任务链 → LLMPromptProcessor → m0。"""
    text = (user_text or "").strip()
    if not text:
        return AgentPlan(ok=False, error="空输入")

    # 0) 严格只读意图（高效型准确优先：已知窄问绝不误绑）
    try:
        from chibyterm.nl_readonly_intent import classify_readonly_intent

        hit = classify_readonly_intent(text, conn_type=conn_type)
        if hit is not None:
            return AgentPlan(
                ok=True,
                explanation=hit.label,
                commands=[_sanitize_agent_command(hit.command)],
                source="strict",
            )
    except Exception as exc:
        logger.debug("classify_readonly_intent 跳过: %s", exc)

    # 1) 预置任务链（与 WS plan 一致）
    if host_obj is not None:
        try:
            chain = try_build_chain_plan(_session_shim(host_obj, conn_type), text)
            if chain:
                steps, explanation, chain_id = chain
                cmds = [_sanitize_agent_command(c) for c in _steps_to_commands(steps)]
                if cmds:
                    return AgentPlan(
                        ok=True,
                        explanation=explanation or f"命中任务链 `{chain_id}`",
                        commands=cmds,
                        source="chain",
                    )
        except Exception as exc:
            logger.debug("try_build_chain_plan 跳过: %s", exc)

    # 2) LLM / 规则（与桌面 Agent 同一处理器）
    profile = _shell_profile(conn_type)
    try:
        result = get_prompt_processor().process(
            text,
            session_context="",
            runtime_hint=(
                f"目标连接类型={conn_type}；请给出可在该主机上直接执行的命令。"
                "多步命令请分行输出。"
            ),
            shell_profile=profile,
        )
    except Exception as exc:
        logger.warning("LLMPromptProcessor 失败: %s", exc)
        return AgentPlan(ok=False, error=str(exc)[:300])

    if result.should_execute and (result.command or "").strip():
        cmds: List[str] = []
        for line in result.command.splitlines():
            s = _sanitize_agent_command(line.strip())
            if s and s not in cmds:
                cmds.append(s)
        if cmds:
            src = "llm" if get_prompt_processor()._llm_available else "rule"
            return AgentPlan(
                ok=True,
                explanation=result.explanation or "Agent 已规划命令",
                commands=cmds[:12],
                source=src,
            )

    # 3) 回退结构化规则（删除/重启等）；需闭源 chiby_mobile（可选）
    try:
        import importlib

        plan_m0 = importlib.import_module("chiby_mobile.planner_m0").plan_m0
    except ImportError:
        plan_m0 = None

    if plan_m0 is not None:
        hid = str(getattr(host_obj, "id", "") or "bound") if host_obj is not None else "bound"
        m0 = plan_m0(text, bound_host_id=hid, known_ids=[hid])
        if m0.kind in ("query", "mutate") and (m0.exec_hint or "").strip():
            return AgentPlan(
                ok=True,
                explanation=m0.reply_text or "规则规划",
                commands=[_sanitize_agent_command(m0.exec_hint.strip())],
                source="m0",
            )
        m0_reply = (m0.reply_text or "") if m0.kind == "chat" else ""
    else:
        m0_reply = ""

    llm_on = bool(get_prompt_processor()._llm_available)
    miss = _plan_miss_message(
        llm_available=llm_on,
        m0_reply=m0_reply,
        processor_msg=(result.explanation or ""),
    )
    return AgentPlan(
        ok=False,
        explanation=miss,
        error=miss,
        source="miss",
    )


_EXAMPLES_ZH = (
    "例如：「内存还剩多少」「哪些进程占用内存高」「磁盘还剩多少」"
    "「系统有几个用户」「当前主机名」。"
)


def _plan_miss_message(
    *,
    llm_available: bool,
    m0_reply: str = "",
    processor_msg: str = "",
) -> str:
    """规划失败时给出可操作原因，避免笼统的「无法理解」。"""
    if m0_reply and ("请说得更具体" in m0_reply or "请先指定主机" in m0_reply):
        return m0_reply.strip()
    # 忽略 llm_shell 旧的笼统文案
    if processor_msg and "无法理解输入" not in processor_msg:
        if "请说得更具体" in processor_msg:
            return processor_msg.strip()
    if not llm_available:
        return (
            "高效型当前未接入大模型，只能识别已登记的运维问法；"
            f"这句话尚未收入规则。{_EXAMPLES_ZH}"
            "也可配置 data/llm_config.json 的 API Key，或切换智能型（需 Hermes）。"
        )
    return (
        "未能规划出可执行命令（大模型无有效输出，规则也未命中）。"
        f"{_EXAMPLES_ZH}"
    )


_FIX_SOURCE_ZH = {
    "remediator": "remediator（结构化修复）",
    "llm": "LLM 直修",
    "heuristic": "启发式回退",
    "knowledge_hub": "知识库命中",
    "custom": "自定义修复",
}


def _format_fix_sources_zh(sources: List[str]) -> str:
    labels: List[str] = []
    for s in sources or []:
        key = (s or "").strip()
        if not key or key in labels:
            continue
        labels.append(_FIX_SOURCE_ZH.get(key, key))
    # 去重保序（按中文标签）
    seen: List[str] = []
    for lb in labels:
        if lb not in seen:
            seen.append(lb)
    return " → ".join(seen)


def run_agent_closure(
    host_obj: Any,
    command: str,
    *,
    trace_id: str = "",
    timeout_sec: float = 120.0,
    max_fix_attempts: int = 2,
    nl_intent_hint: str = "",
    on_step: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str, List[str]]:
    """在主机上执行：网关 → oneshot → 闭环自愈。

    返回 (ok, 可读摘要, 实际尝试过的命令列表)。
    """
    cmd = (command or "").strip()
    if not cmd:
        return False, "命令为空", []
    host_id = str(getattr(host_obj, "id", "") or "")
    ct = getattr(host_obj, "conn_type", "ssh")
    if hasattr(ct, "value"):
        ct = ct.value
    ct_s = str(ct or "ssh").lower()
    tid = (trace_id or ("ag_" + uuid.uuid4().hex[:12])).strip()

    danger, warn = command_line_danger(cmd)
    if danger:
        return False, warn or "危险命令已拦截", [cmd]

    def gateway_allow(line: str) -> Tuple[bool, str]:
        out = gateway_evaluate(
            ExecutionRequest(
                trace_id=tid,
                session_id=f"mobile_agent:{host_id}",
                command_line=line,
                source="mobile_agent",
                conn_type=ct_s,
                host_id=host_id,
                plan_id=None,
            ),
        )
        return out.allowed, out.reason or ""

    tried: List[str] = []

    def run_sync() -> ClosureRunResult:
        ex = build_oneshot_from_pydantic_host(host_obj)
        ex.connect()
        try:

            def execute_one(c: str) -> CoreExecResult:
                tried.append(c)
                if on_step:
                    with_contextlib_suppress(on_step, f"执行: {c[:120]}")
                return ex.run_command(c, RunOptions(timeout_sec=float(timeout_sec)))

            distro_kw = {}
            if ct_s != "winrm":
                dp = getattr(host_obj, "distro_profile", None)
                if dp is not None:
                    fam = (getattr(dp, "family", None) or "").strip()
                    pkg = (getattr(dp, "pkg_manager", None) or "").strip()
                    if fam and fam != "linux_generic":
                        distro_kw["distro_family"] = fam
                    if pkg and pkg != "unknown":
                        distro_kw["pkg_manager"] = pkg
            return run_closure_retry_loop(
                trace_id=tid,
                initial_command=cmd,
                execute=execute_one,
                gateway_allow=gateway_allow,
                shell_profile="powershell" if ct_s == "winrm" else "unix",
                nl_intent_hint=nl_intent_hint or cmd[:200],
                session_id=f"mobile_agent:{host_id}",
                max_fix_attempts=max(0, int(max_fix_attempts)),
                success_mode="exit_code",
                archive_kb=False,
                **distro_kw,
            )
        finally:
            try:
                ex.close()
            except Exception:
                pass

    try:
        result = run_sync()
    except Exception as exc:
        logger.exception("run_agent_closure 失败 host=%s", host_id)
        return False, f"Agent 闭环执行异常：{exc}"[:400], tried or [cmd]

    ok = bool(result.ok)
    parts: List[str] = []
    if result.stop_reason:
        parts.append(f"结束原因：{result.stop_reason}")
    src_line = _format_fix_sources_zh(list(getattr(result, "fix_sources", None) or []))
    # 仅自愈成功时展示来源；失败时写「知识库命中」会误导（常见于壳串台）
    if src_line and ok and result.stop_reason == "success_after_fix":
        parts.append(f"自愈来源：{src_line}")
    if result.final_payload:
        fp = result.final_payload
        parts.append(f"exit={fp.exit_code}")
        out = (fp.stdout or "")[-1500:]
        err = (fp.stderr or "")[-800:]
        if out:
            parts.append(out.strip())
        if err:
            parts.append(("stderr:\n" if out else "") + err.strip())
    elif result.steps:
        last = result.steps[-1]
        if last.result:
            parts.append(f"exit={last.result.exit_code}")
            parts.append((last.result.stdout or "")[-1200:])
            if last.result.stderr:
                parts.append((last.result.stderr or "")[-600:])
    summary = "\n".join(p for p in parts if p).strip() or ("成功" if ok else "失败")
    tag = "Agent闭环·成功" if ok else "Agent闭环·失败"
    fix_n = sum(1 for s in result.steps if s.phase == "fix")
    head = f"[{tag}] host=`{host_id}` fix_rounds={fix_n}\n$ {cmd}"
    return ok, head + "\n" + summary, tried or [cmd]


def with_contextlib_suppress(fn: Callable[[str], None], msg: str) -> None:
    try:
        fn(msg)
    except Exception:
        pass
