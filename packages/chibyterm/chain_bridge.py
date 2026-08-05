"""将 chibycore 预置任务链转为终端 WebSocket「计划模式」步骤（bash 或 WinRM/PowerShell）。"""
from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from chibycore.chains import ChainExecutor, ChainPlanner, TASK_CHAINS
from chibycore.script_generator import build_command, build_rollback_command, build_verify_command
from chibycore.script_generator_pwsh import build_powershell_command, build_powershell_verify_command

from chibyterm.llm_shell import classify_command_risk

if TYPE_CHECKING:
    from chibyterm.models import TerminalSession

logger = logging.getLogger(__name__)


def _chain_unsupported(session: "TerminalSession") -> bool:
    """Windows 本地管道 shell 无 bash 链；WinRM 已支持 PowerShell 链。"""
    from chibyterm.models import ConnType

    if session.conn_type == ConnType.LOCAL and sys.platform == "win32":
        return True
    return False


def try_build_chain_plan(
    session: "TerminalSession",
    user_text: str,
) -> Optional[Tuple[List[Dict[str, Any]], str, str]]:
    """
    若自然语言命中 TASK_CHAINS，展开为与 llm_plan 兼容的 steps。

    返回 (steps, explanation, chain_id) 或 None。
    """
    if not user_text or not user_text.strip():
        return None
    if _chain_unsupported(session):
        return None

    planner = ChainPlanner()
    chain, params = planner.match_chain(user_text.strip())
    if not chain:
        return None

    raw_plan = planner.build_plan(chain, params)
    executor = ChainExecutor(
        session.host or "127.0.0.1",
        session.username or "",
        session.password or "",
    )
    sorted_defs = executor._topological_sort(raw_plan)

    pw = session.password or ""
    steps_out: List[Dict[str, Any]] = []

    from chibyterm.models import ConnType

    use_ps = session.conn_type == ConnType.WINRM

    for step_def in sorted_defs:
        action = step_def["action"]
        merged = dict(step_def.get("params") or {})
        desc = step_def.get("description") or action.value
        cmd = ""
        vcmd: Optional[str] = None
        rb: Optional[str] = None
        try:
            if use_ps:
                ps = build_powershell_command(action, merged, pw)
                if not ps:
                    logger.debug("chain_bridge WinRM 跳过无 PS 映射的动作: %s", action)
                    continue
                cmd = ps.strip()
                vcmd = build_powershell_verify_command(action, merged)
            else:
                cmd = build_command(action, merged, pw).strip()
                vcmd = build_verify_command(action, merged)
                rb = build_rollback_command(action, merged)
        except Exception as e:
            logger.warning("chain_bridge 生成命令失败: %s", e)
            continue
        if not cmd or cmd.startswith("echo '未知动作"):
            continue
        level, w = classify_command_risk(cmd)
        title = desc if len(desc) <= 56 else desc[:53] + "…"
        row: Dict[str, Any] = {
            "index": len(steps_out),
            "title": title,
            "command": cmd,
            "dangerous": level == "HIGH",
            "confirm_required": level in ("MEDIUM", "HIGH"),
            "risk": level,
            "warning": w or "",
        }
        if vcmd:
            row["verify_command"] = vcmd
            if use_ps:
                row["verify_expect_substring"] = "OK"
        if rb and not use_ps:
            row["rollback_command"] = rb
        steps_out.append(row)

    if not steps_out:
        return None

    chain_id = next((cid for cid, c in TASK_CHAINS.items() if c is chain), "unknown")
    explanation = f"已匹配预置任务链「{chain.name}」：{chain.description}"
    return steps_out, explanation, chain_id
