"""闭环成功 → KB 候选队列：脱敏摘要 + 人工批准后正式入库。

环境：OPS_KB_PENDING_ON_CLOSURE=0 关闭入队（默认开启）。
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from chibycore.knowledge_hub.ingestion import KnowledgeIngester
from chibycore.knowledge_hub.models import (
    KBCategory,
    KBConfidence,
    KBEntry,
    KBPendingCandidate,
    IngestSource,
    PendingKBStatus,
)
from chibycore.knowledge_hub.storage import KnowledgeHubStorage
from chibycore.redaction import redact_command_text
from chibycore.output_budget import (
    API_EXEC_IO_TAIL_CHARS,
    CLOSURE_ARCHIVE_TAIL_CHARS,
    CLOSURE_STDIO_TAIL_CHARS,
    KB_PENDING_FINAL_IO_TAIL_CHARS,
)

logger = logging.getLogger(__name__)


def closure_pending_enabled() -> bool:
    return os.environ.get("OPS_KB_PENDING_ON_CLOSURE", "1").strip() not in ("0", "false", "no")


def _excerpt(text: str, n: int = 2400) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 3] + "..."


def _infer_category_from_text(blob: str) -> KBCategory:
    """复用 KnowledgeIngester 的分类启发规则。"""
    steps_dummy: List[Dict[str, Any]] = [{"command": blob}]
    return KnowledgeIngester._infer_category(steps_dummy, blob)


def build_kb_pending_candidate(
    *,
    steps: List[Any],
    final_payload: Optional[Any],
    trace_id: str,
    nl_intent_hint: Optional[str],
    shell_profile: str,
    stop_reason: str,
    kb_pending_meta: Optional[Dict[str, Any]],
) -> KBPendingCandidate:
    """从闭环步骤构造候选条目（命令与输出均脱敏）。"""
    meta = dict(kb_pending_meta or {})
    cmd_lines: List[str] = []
    digest: List[Dict[str, Any]] = []
    for st in steps:
        red_cmd = redact_command_text(getattr(st, "command", "") or "", max_len=CLOSURE_ARCHIVE_TAIL_CHARS)
        cmd_lines.append(red_cmd)
        rec: Dict[str, Any] = {
            "phase": getattr(st, "phase", ""),
            "fix_round": getattr(st, "fix_round", 0),
            "gateway_allowed": getattr(st, "gateway_allowed", False),
            "command_redacted": red_cmd,
        }
        res = getattr(st, "result", None)
        if res is not None:
            rec["exit_code"] = getattr(res, "exit_code", None)
            so = redact_command_text(getattr(res, "stdout", "") or "", max_len=12000)
            se = redact_command_text(getattr(res, "stderr", "") or "", max_len=12000)
            rec["stdout_excerpt"] = _excerpt(so, 1200)
            rec["stderr_excerpt"] = _excerpt(se, 1200)
        digest.append(rec)

    command_chain = "\n".join(cmd_lines)

    initial_stderr = ""
    initial_stdout = ""
    for st in steps:
        if getattr(st, "phase", "") == "initial" and getattr(st, "result", None):
            initial_stderr = getattr(st.result, "stderr", "") or ""
            initial_stdout = getattr(st.result, "stdout", "") or ""
            break

    fp = final_payload
    out_parts: List[str] = []
    if initial_stderr or initial_stdout:
        mix = redact_command_text(
            _excerpt((initial_stderr or "") + "\n" + (initial_stdout or ""), CLOSURE_STDIO_TAIL_CHARS),
            max_len=CLOSURE_ARCHIVE_TAIL_CHARS,
        )
        out_parts.append("首轮输出摘录：\n" + mix)
    if fp is not None:
        tail = redact_command_text(
            _excerpt(
                ((getattr(fp, "stderr", "") or "") + "\n" + (getattr(fp, "stdout", "") or ""))[
                    -KB_PENDING_FINAL_IO_TAIL_CHARS:
                ]
            ),
            max_len=CLOSURE_ARCHIVE_TAIL_CHARS,
        )
        out_parts.append("最终有效输出摘录：\n" + tail)
    output_summary = "\n\n".join(out_parts)[:12000]

    hint = (nl_intent_hint or "").strip()
    if hint:
        title = hint[:80] if len(hint) <= 80 else hint[:77] + "..."
    else:
        title = f"闭环修复 · {trace_id[:16]}"

    blob_for_cat = command_chain + "\n" + output_summary[:2000]
    cat = _infer_category_from_text(blob_for_cat)

    symptom = ""
    if initial_stderr:
        symptom = "首轮错误/输出：" + _excerpt(redact_command_text(initial_stderr, API_EXEC_IO_TAIL_CHARS), 600)
    elif hint:
        symptom = f"自然语言意图：{hint}"
    else:
        symptom = "闭环执行成功（首轮输出见 output_summary）"

    root_cause = "由闭环自动判定成功"
    if stop_reason == "success_after_fix":
        root_cause = "首轮未满足成功条件，经自动修复轮次后成功"

    remediation = command_chain
    if fp is not None and getattr(fp, "effective_command", None):
        remediation = redact_command_text(fp.effective_command or "", max_len=16000)

    tags = ["closure-repair", "pending-review", shell_profile]
    ct = meta.get("conn_type")
    if ct:
        tags.append(str(ct))
    ep = meta.get("entrypoint")
    if ep:
        tags.append(str(ep))

    return KBPendingCandidate(
        id=str(uuid.uuid4())[:12],
        trace_id=trace_id,
        status=PendingKBStatus.PENDING,
        title=title,
        tags=tags,
        host_profile={k: v for k, v in meta.items() if k not in ("password",)},
        command_chain_redacted=command_chain,
        output_summary=output_summary,
        symptom=symptom,
        root_cause=root_cause,
        remediation=remediation,
        suggested_category=cat.value,
        nl_intent_hint=nl_intent_hint,
        closure_stop_reason=stop_reason,
        step_count=len(steps),
        shell_profile=shell_profile,
        raw_steps_digest=digest,
    )


def try_enqueue_closure_pending_candidate(
    *,
    steps: List[Any],
    final_payload: Optional[Any],
    trace_id: str,
    nl_intent_hint: Optional[str],
    shell_profile: str,
    stop_reason: str,
    kb_pending_meta: Optional[Dict[str, Any]],
) -> Optional[str]:
    """构建候选并入队 SQLite；返回 candidate id。"""
    if not closure_pending_enabled():
        return None
    try:
        cand = build_kb_pending_candidate(
            steps=steps,
            final_payload=final_payload,
            trace_id=trace_id,
            nl_intent_hint=nl_intent_hint,
            shell_profile=shell_profile,
            stop_reason=stop_reason,
            kb_pending_meta=kb_pending_meta,
        )
        storage = KnowledgeHubStorage.get_instance()
        storage.save_pending_candidate(cand)
        logger.info(
            "[KnowledgeHub] 闭环 KB 候选已入队 id=%s trace_id=%s", cand.id, trace_id
        )
        return cand.id
    except Exception:
        logger.exception("try_enqueue_closure_pending_candidate failed trace_id=%s", trace_id)
        return None


def pending_candidate_to_kb_entry(
    cand: KBPendingCandidate,
    *,
    category: Optional[KBCategory] = None,
    title_override: Optional[str] = None,
) -> KBEntry:
    """候选 → 正式 KBEntry（供批准 API 使用）。"""
    if category is not None:
        cat = category
    else:
        try:
            cat = KBCategory(cand.suggested_category)
        except ValueError:
            cat = KBCategory.FAILURE_RECOVERY

    return KBEntry(
        title=(title_override or cand.title)[:500],
        category=cat,
        symptom=cand.symptom,
        root_cause=cand.root_cause,
        remediation=cand.remediation,
        verify_method=_excerpt(cand.output_summary, 1500),
        applicable_os=_os_hints_from_profile(cand),
        tags=list({*cand.tags, "closure-approved"} - {"pending-review"}),
        confidence=KBConfidence.MEDIUM,
        source=IngestSource.CLOSURE_APPROVED.value,
        source_id=cand.trace_id,
        notes=f"来自闭环候选 {cand.id}；trace={cand.trace_id}",
        original_command=cand.command_chain_redacted[:2048],
    )


def _os_hints_from_profile(cand: KBPendingCandidate) -> List[str]:
    sp = (cand.shell_profile or "").lower()
    hp = cand.host_profile or {}
    out: List[str] = []
    if "powershell" in sp or hp.get("conn_type") == "winrm":
        out.append("windows")
    if "unix" in sp or hp.get("conn_type") in ("ssh", None):
        out.append("linux")
    return out or ["linux"]
