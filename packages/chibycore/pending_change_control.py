"""变更冻结窗口内被网关拦截的命令：待审批队列（JSON 持久化）。

队列文件：data/pending_change_control.json（数组，最多保留 500 条 pending）。
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _path() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "pending_change_control.json"


def _load_all() -> List[Dict[str, Any]]:
    p = _path()
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("pending_change_control 读取失败: %s", e)
        return []


def _save(rows: List[Dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_pending_change(
    *,
    trace_id: str,
    session_id: str,
    command_line: str,
    source: str,
    conn_type: str,
    host_id: Optional[str],
    plan_id: Optional[str],
    nl_intent: Optional[str] = None,
) -> str:
    """入队，返回 pending_id。"""
    pid = "pc_" + uuid.uuid4().hex[:16]
    rec = {
        "pending_id": pid,
        "trace_id": trace_id,
        "session_id": session_id,
        "command_line": command_line,
        "source": source,
        "conn_type": conn_type,
        "host_id": host_id,
        "plan_id": plan_id,
        "nl_intent": nl_intent,
        "status": "pending",
    }
    with _LOCK:
        rows = _load_all()
        rows.append(rec)
        # 仅保留最近 pending + 少量历史由运维清理
        pend = [r for r in rows if r.get("status") == "pending"]
        hist = [r for r in rows if r.get("status") != "pending"][-80:]
        merged = pend[-420:] + hist
        _save(merged[-500:])
    return pid


def list_pending_change(session_id: Optional[str] = None) -> List[Dict[str, Any]]:
    with _LOCK:
        rows = _load_all()
    out = [r for r in rows if r.get("status") == "pending"]
    if session_id:
        out = [r for r in out if r.get("session_id") == session_id]
    return sorted(out, key=lambda x: x.get("pending_id", ""), reverse=True)


def get_pending_change(pending_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        for r in _load_all():
            if r.get("pending_id") == pending_id and r.get("status") == "pending":
                return dict(r)
    return None


def pop_pending_change(pending_id: str) -> Optional[Dict[str, Any]]:
    """取出并移除 pending 记录（批准后执行）。"""
    with _LOCK:
        rows = _load_all()
        found = None
        rest: List[Dict[str, Any]] = []
        for r in rows:
            if r.get("pending_id") == pending_id and r.get("status") == "pending":
                found = dict(r)
                continue
            rest.append(r)
        if found:
            _save(rest)
        return found


def mark_rejected(pending_id: str) -> bool:
    with _LOCK:
        rows = _load_all()
        ok = False
        for r in rows:
            if r.get("pending_id") == pending_id and r.get("status") == "pending":
                r["status"] = "rejected"
                ok = True
                break
        if ok:
            _save(rows)
        return ok
