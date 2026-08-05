"""将计划多步命令合并为单条可执行命令，并给出风险等级（命令集汇总执行）。"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from chibyterm.shell_context import ALLOWED_TARGET_OS, infer_default_target_os

RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


def _session_target_os(session: Any) -> str:
    if session is None:
        return "linux"
    v = (getattr(session, "target_os", None) or "").strip()
    if v in ALLOWED_TARGET_OS:
        return v
    return infer_default_target_os(session)


def _is_windowsish_target(os_id: str) -> bool:
    return os_id == "windows"


def aggregate_plan_commands(
    steps: List[Dict[str, Any]],
    *,
    target_os: str,
) -> str:
    """
    按目标 OS 将步骤中的 command 合并为一条。
    - 类 Unix：使用 &&（前一步失败则停止，更接近「有依赖」的保守语义）
    - Windows（PowerShell）：使用 ; 顺序执行
    """
    parts: List[str] = []
    for st in steps:
        line = (st.get("command") or "").strip()
        if line:
            parts.append(line)
    if not parts:
        return ""
    if _is_windowsish_target(target_os):
        return "; ".join(parts)
    return " && ".join(parts)


def infer_command_set_risk(steps: List[Dict[str, Any]]) -> RiskLevel:
    """取各步风险上限：优先 step.risk；否则 dangerous→HIGH、confirm_required→MEDIUM。"""
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    best: RiskLevel = "LOW"
    for st in steps:
        rk = str(st.get("risk") or "").strip().upper()
        if rk in rank and rank[rk] > rank[best]:
            best = rk  # type: ignore[assignment]
            continue
        if st.get("dangerous") and rank["HIGH"] > rank[best]:
            best = "HIGH"
        elif st.get("confirm_required") and rank["MEDIUM"] > rank[best]:
            best = "MEDIUM"
    return best


def build_command_set_meta(
    steps: List[Dict[str, Any]],
    target_os: str,
    *,
    description: str = "",
) -> Optional[Dict[str, Any]]:
    """
    生成 llm_plan 附带的 command_set 字段；无有效命令时返回 None。
    """
    cmds = [(st.get("command") or "").strip() for st in steps]
    cmds = [c for c in cmds if c]
    if not cmds:
        return None
    combined = aggregate_plan_commands(steps, target_os=target_os)
    if not combined.strip():
        return None
    risk = infer_command_set_risk(steps)
    shell: str = "powershell" if _is_windowsish_target(target_os) else "bash"
    os_out: str = "windows" if _is_windowsish_target(target_os) else "linux"
    return {
        "combined_command": combined,
        "os": os_out,
        "shell": shell,
        "commands": cmds,
        "risk": risk,
        "description": (description or "").strip()[:2000],
    }


def enrich_llm_plan_payload(
    session: Any,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """为即将下发的 llm_plan JSON 附加 command_set（就地拷贝）。"""
    steps = payload.get("steps") or []
    if not isinstance(steps, list) or not steps:
        return payload
    tos = _session_target_os(session)
    desc = (payload.get("explanation") or "")[:500]
    cs = build_command_set_meta(steps, tos, description=desc)
    if not cs:
        return payload
    out = dict(payload)
    out["command_set"] = cs
    return out
