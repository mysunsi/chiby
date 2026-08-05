"""闭环失败时优先走 remediator：结构化错误 + 向量 KB + few-shot + LLMRemediationJSON。"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import List

from chibycore.closure_service import ClosurePayload
from chibycore.llm_providers import get_llm, remediator_litellm_credentials

logger = logging.getLogger(__name__)


def _project_root() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()


def _kb_default_path() -> Path:
    raw = (os.environ.get("OPS_REMEDIATION_KB_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _project_root() / "data" / "remediation_kb.db"


def _err_snip(cp: ClosurePayload) -> str:
    blob = ((cp.stderr or "") + "\n" + (cp.stdout or "")).strip()
    if len(blob) > 1200:
        blob = blob[-1197:] + "..."
    tr = getattr(cp, "transport", "") or ""
    return f"exit={cp.exit_code} transport={tr} :: {blob}"


def _build_remediation_history(history: List[ClosurePayload]):
    from remediator.remediation.models import RemediationHistory

    rh = RemediationHistory()
    if not history:
        return rh
    h0 = history[0]
    rh.append("original_command", (h0.effective_command or h0.raw_command or "").strip())
    rh.append("error", _err_snip(h0))
    for i in range(1, len(history)):
        hi = history[i]
        rh.append("fix_command", (hi.effective_command or "").strip())
        rh.append("error", _err_snip(hi))
    return rh


def _is_multiline_shell_block(normalized: str, non_comment_lines: List[str]) -> bool:
    """多行 if/for/case 等必须整段执行，不能按行拆成多条独立命令。"""
    if len(non_comment_lines) <= 1:
        return False
    low = normalized.lower()
    if "then" in low and "fi" in low:
        return True
    if "do" in low and "done" in low:
        return True
    if re.search(r"\bcase\b", low) and "esac" in low:
        return True
    first = non_comment_lines[0].lstrip()
    if first.startswith("if ") or first.startswith("if["):
        return True
    return False


def _split_fixed_commands(fixed: str, max_n: int = 3) -> List[str]:
    t = (fixed or "").strip()
    if not t:
        return []
    norm = t.replace("\r\n", "\n")
    lines: List[str] = []
    for ln in norm.split("\n"):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
    if not lines:
        return []
    # 多行脚本：返回完整文本一条，避免把「if/then/else/fi」拆碎且被 max_n 截断
    if _is_multiline_shell_block(norm, lines):
        return [norm.strip()]
    if len(lines) == 1:
        return [lines[0]]
    return lines[:max_n]


def call_remediator_for_fix_commands(
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
) -> List[str]:
    if not history:
        return []
    mgr = get_llm()
    if not mgr.is_available:
        return []
    creds = remediator_litellm_credentials()
    if creds is None:
        return []

    cp = history[-1]
    cmd = (cp.effective_command or cp.raw_command or "").strip()
    rc = cp.exit_code
    if rc is None:
        rc = -1

    try:
        from remediator.remediation.knowledge_base import RemediationKnowledgeBase
        from remediator.remediation.llm_agent import propose_remediation
        from remediator.remediation.models import EnvironmentSnapshot
        from remediator.remediation.parser import parse_execution_error
        from remediator.remediation.prompt_enrichment import build_remediation_prompt_suffix
    except ModuleNotFoundError as ex:
        logger.info("remediator fix skipped (依赖缺失): %s", ex)
        return []

    se = parse_execution_error(
        command=cmd,
        return_code=int(rc),
        stdout=cp.stdout or "",
        stderr=cp.stderr or "",
    )
    rh = _build_remediation_history(history)
    env = EnvironmentSnapshot(
        os_name="Windows" if (shell_profile or "").lower() == "powershell" else "Linux",
        os_version="",
        shell="powershell" if (shell_profile or "").lower() == "powershell" else "bash",
        current_user="",
        is_root_or_sudo=False,
        cwd="/",
    )
    kb = RemediationKnowledgeBase(_kb_default_path())
    suffix = build_remediation_prompt_suffix(kb, se, env)
    model = (creds.get("model") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    api_base = creds.get("api_base")
    api_key = creds.get("api_key")

    try:
        out = propose_remediation(
            se,
            rh,
            env,
            model=model,
            api_base=api_base,
            api_key=api_key,
            knowledge_base=kb,
            prompt_suffix=suffix,
        )
    except Exception as ex:  # pragma: no cover
        logger.warning("remediator propose_remediation failed: %s", ex)
        return []
    return _split_fixed_commands(out.fixed_command or "", 3)


def remediator_fix_enabled() -> bool:
    v = (os.environ.get("OPS_CLOSURE_REMEDIATOR_FIX") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")
