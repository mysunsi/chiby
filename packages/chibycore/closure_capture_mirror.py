"""闭环输出镜像：格式化辅助 +（可选）写入交互会话终端。

默认**不**向左侧终端注入 oneshot 闭环的 stdout/页脚（体验差、与 PTY 历史混淆）；
右侧时间线仍靠 mirror_session_id + repair_* / SSE。
若需旧行为，设置环境变量 OPS_CLOSURE_MIRROR_TERMINAL=1。
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from chibycore.closure_service import ClosurePayload


def closure_terminal_mirror_enabled() -> bool:
    """是否把闭环流/页脚写入左侧终端（及同源 output_capture）。默认关闭。"""
    v = (os.environ.get("OPS_CLOSURE_MIRROR_TERMINAL") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def closure_step_banner_cn(*, phase: str, fix_round: int, gateway_allowed: bool) -> str:
    """左侧终端展示用中文短标题。"""
    if not gateway_allowed:
        return "闭环 · 网关拒绝"
    if phase == "initial":
        return "闭环 · 首轮执行"
    if phase == "fix" and fix_round > 0:
        return f"闭环 · 自动修复 第 {fix_round} 轮"
    return "闭环"


def format_closure_step_for_capture(
    cp: ClosurePayload,
    *,
    banner_title: Optional[str] = None,
) -> str:
    title = banner_title or "闭环"
    return (
        f"\r\n\r\n\x1b[96m━━ {title} ━━\x1b[0m\r\n"
        f"\x1b[90mexit={cp.exit_code} transport={cp.transport}\x1b[0m\r\n"
        f"\x1b[90m--- stdout ---\x1b[0m\r\n{(cp.stdout or '')[-12000:]}\r\n"
        f"\x1b[90m--- stderr ---\x1b[0m\r\n{(cp.stderr or '')[-12000:]}\r\n\r\n"
    )


def format_gateway_denial_capture(
    *,
    banner_title: str,
    command: str,
    gateway_reason: str,
    gateway_detail: Optional[Dict[str, Any]] = None,
) -> str:
    cmd_snip = (command or "")[:4000]
    reason = (gateway_reason or "").strip()[:2000]
    extra = ""
    gd = gateway_detail or {}
    if gd:
        parts: list[str] = []
        cat = str(gd.get("denial_category") or "").strip()
        if cat:
            parts.append("类别: " + cat)
        rk = str(gd.get("rule_kind") or "").strip()
        if rk:
            parts.append("规则类型: " + rk)
        mp = str(gd.get("matched_pattern") or "").strip()
        if mp:
            parts.append("匹配: " + mp[:400])
        if gd.get("override_requires_approval"):
            parts.append("需审批后放行")
        if gd.get("progressive_policy_hint"):
            parts.append("策略提示: " + str(gd.get("progressive_policy_hint"))[:200])
        if parts:
            extra = "\r\n" + "\r\n".join(parts) + "\r\n"
    return (
        f"\r\n\r\n\x1b[96m━━ {banner_title} ━━\x1b[0m\r\n"
        f"\x1b[33m网关拒绝\x1b[0m\r\n\x1b[90m命令:\x1b[0m {cmd_snip}\r\n"
        f"\x1b[90m原因:\x1b[0m {reason}\r\n"
        f"{extra}\r\n"
    )


def format_mirror_io_fragment(stream: str, text: str) -> str:
    """闭环流式镜像：单片段 ANSI（不含擦行）。"""
    if text == "":
        return ""
    if stream == "stderr":
        return f"\x1b[33m{text}\x1b[0m"
    return text


def _mirror_footer_command_one_line(cmd: str, max_len: int = 2000) -> str:
    """页脚单行展示用：压平换行，截断过长命令。"""
    s = " ".join((cmd or "").replace("\r", " ").replace("\n", " ").split())
    if len(s) > max_len:
        return s[: max_len - 1] + "…"
    return s


def format_mirror_step_footer_streaming(step: Any) -> str:
    """流式闭环每步结束时的简短页脚；无可展示内容时返回空串。"""
    phase = str(getattr(step, "phase", "") or "")
    fix_round = int(getattr(step, "fix_round", 0) or 0)
    gok = bool(getattr(step, "gateway_allowed", False))
    cmd = getattr(step, "command", "") or ""
    greason = getattr(step, "gateway_reason", "") or ""
    gdetail = getattr(step, "gateway_detail", None)
    banner = closure_step_banner_cn(
        phase=phase, fix_round=fix_round, gateway_allowed=gok
    )
    if not gok:
        return format_gateway_denial_capture(
            banner_title=banner,
            command=cmd,
            gateway_reason=greason,
            gateway_detail=gdetail if isinstance(gdetail, dict) else None,
        )
    cp = getattr(step, "payload", None)
    if cp is None:
        return ""
    cmd_line = _mirror_footer_command_one_line(cmd)
    cmd_block = f"\x1b[90m命令:\x1b[0m {cmd_line}\r\n" if cmd_line else ""
    return (
        f"\r\n\r\n\x1b[96m━━ {banner} · 完成 ━━\x1b[0m\r\n"
        f"{cmd_block}"
        f"\x1b[90mexit={cp.exit_code} transport={cp.transport}"
        f" · 本步输出见上文实时流\x1b[0m\r\n\r\n"
    )


def mirror_gateway_denial_capture(
    session_mgr,
    session_id: str,
    *,
    banner_title: str,
    command: str,
    gateway_reason: str,
    gateway_detail: Optional[Dict[str, Any]] = None,
) -> None:
    if not session_id or session_mgr is None:
        return
    session_mgr.append_output_capture(
        session_id,
        format_gateway_denial_capture(
            banner_title=banner_title,
            command=command,
            gateway_reason=gateway_reason,
            gateway_detail=gateway_detail,
        ),
    )


def mirror_closure_io_to_terminal(
    session_mgr,
    loop: Any,
    session_id: str,
    stream: str,
    text: str,
) -> None:
    """可选：将子进程流式块写入左侧终端。默认关闭，见 closure_terminal_mirror_enabled。"""
    if not closure_terminal_mirror_enabled():
        return
    if not session_id or session_mgr is None or text == "":
        return
    frag = format_mirror_io_fragment(stream, text)
    session_mgr.schedule_terminal_output(
        loop, session_id, frag, closure_mirror=True
    )


def mirror_closure_step_after_streaming(
    session_mgr,
    loop: Any,
    session_id: str,
    step: Any,
) -> None:
    """可选：流式结束后追加页脚。默认关闭。"""
    if not closure_terminal_mirror_enabled():
        return
    if not session_id or session_mgr is None:
        return
    text = format_mirror_step_footer_streaming(step)
    if text:
        session_mgr.schedule_terminal_output(
            loop, session_id, text, closure_mirror=True
        )


def mirror_closure_step_to_session(session_mgr, session_id: str, step: Any) -> None:
    """
    可选：将单步闭环写入会话 capture（duck-type ClosureStepRecord）。
    默认关闭，避免污染左侧终端与会话上下文。
    """
    if not closure_terminal_mirror_enabled():
        return
    if not session_id or session_mgr is None:
        return
    phase = str(getattr(step, "phase", "") or "")
    fix_round = int(getattr(step, "fix_round", 0) or 0)
    gok = bool(getattr(step, "gateway_allowed", False))
    cmd = getattr(step, "command", "") or ""
    greason = getattr(step, "gateway_reason", "") or ""
    gdetail = getattr(step, "gateway_detail", None)
    banner = closure_step_banner_cn(
        phase=phase, fix_round=fix_round, gateway_allowed=gok
    )
    if not gok:
        mirror_gateway_denial_capture(
            session_mgr,
            session_id,
            banner_title=banner,
            command=cmd,
            gateway_reason=greason,
            gateway_detail=gdetail if isinstance(gdetail, dict) else None,
        )
        return
    cp = getattr(step, "payload", None)
    if cp is not None:
        session_mgr.append_output_capture(
            session_id,
            format_closure_step_for_capture(cp, banner_title=banner),
        )


def mirror_payload_to_session(session_mgr, session_id: str, cp: ClosurePayload) -> None:
    """兼容旧调用：无步骤上下文时使用默认标题。默认关闭终端镜像。"""
    if not closure_terminal_mirror_enabled():
        return
    if not session_id or session_mgr is None:
        return
    session_mgr.append_output_capture(
        session_id,
        format_closure_step_for_capture(cp, banner_title="闭环"),
    )
