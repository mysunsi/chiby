"""人机共编闭环：修复意图预览阶段阻塞等待用户采纳 / 改写 / 中止。

由 closure_retry_loop 工作线程调用 ``wait_interactive_resume``；
FastAPI ``POST /api/closure-interactive/{trace_id}/resume`` 调用 ``submit_interactive_resume``。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

_lock = threading.Lock()
_pending: Dict[str, Dict[str, Any]] = {}


def interactive_pause_begin(trace_id: str) -> None:
    """进入「待人机确认」状态（同一 trace_id 每轮修复前可重复调用）。"""
    ev = threading.Event()
    with _lock:
        _pending[trace_id] = {"event": ev, "resume": None, "t0": time.monotonic()}


def submit_interactive_resume(trace_id: str, resume: Dict[str, Any]) -> bool:
    """用户在前端点击采纳/改写/中止后调用，唤醒工作线程。"""
    with _lock:
        st = _pending.get(trace_id)
    if not st:
        return False
    st["resume"] = dict(resume)
    st["event"].set()
    return True


def wait_interactive_resume(trace_id: str, *, timeout_sec: Optional[float] = None) -> Dict[str, Any]:
    """阻塞直至用户提交或超时；返回 dict 至少含 ``action``。"""
    with _lock:
        st = _pending.get(trace_id)
    if not st:
        return {"action": "abort", "reason": "no_pending"}
    ev = st["event"]
    ok = ev.wait(timeout=timeout_sec)
    with _lock:
        st2 = _pending.pop(trace_id, None)
    if not ok:
        return {"action": "abort", "reason": "timeout"}
    if not st2:
        return {"action": "abort", "reason": "missing_state"}
    res = st2.get("resume") or {}
    return res if isinstance(res, dict) else {"action": "abort", "reason": "bad_resume"}


def peek_pause_registered(trace_id: str) -> bool:
    with _lock:
        return trace_id in _pending
