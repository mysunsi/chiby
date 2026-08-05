"""平台统一审计流水（Fleet / AI 诊断 / 定时 / 审批 / 终端 / 知识入库）。

写入 ``data/audit/platform_audit.jsonl``；与 ops_audit / mobile_audit 并存，
本模块提供跨入口的统一事件名与查询面（``/api/audit``）。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence

logger = logging.getLogger(__name__)

EVENT_TYPES = (
    "fleet_execute",
    "web_terminal_session",
    "ai_diagnosis",
    "scheduled_task_run",
    "permission_granted",
    "permission_denied",
    "knowledge_ingest",
)

Outcome = Literal["success", "failure", "partial", "cancelled"]


def _repo_root() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()


def default_platform_audit_path() -> Path:
    p = os.environ.get("PLATFORM_AUDIT_FILE", "").strip()
    if p:
        return Path(p)
    return _repo_root() / "data" / "audit" / "platform_audit.jsonl"


def _max_bytes() -> int:
    try:
        mb = int(os.environ.get("PLATFORM_AUDIT_MAX_MB", "80"))
    except ValueError:
        mb = 80
    return max(1, mb) * 1024 * 1024


def new_trace_id() -> str:
    return "pat_" + uuid.uuid4().hex[:16]


def build_audit_event(
    event_type: str,
    *,
    trace_id: str = "",
    user_id: str = "",
    host_ids: Optional[Sequence[str]] = None,
    host_scope: Optional[Dict[str, Any]] = None,
    command: str = "",
    result_summary: str = "",
    outcome: str = "success",
    duration_ms: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    et = (event_type or "").strip()
    if et not in EVENT_TYPES:
        # 允许扩展，但规范名优先
        et = et or "unknown"
    oc = (outcome or "success").strip().lower()
    if oc not in ("success", "failure", "partial", "cancelled"):
        oc = "success"
    return {
        "trace_id": (trace_id or "").strip() or new_trace_id(),
        "event_type": et,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": (user_id or "").strip(),
        "host_ids": [str(x).strip() for x in (host_ids or []) if str(x).strip()],
        "host_scope": dict(host_scope) if isinstance(host_scope, dict) else None,
        "command": (command or "")[:500],
        "result_summary": (result_summary or "")[:800],
        "outcome": oc,
        "duration_ms": max(0, int(duration_ms or 0)),
        "metadata": dict(metadata or {}),
    }


class PlatformAuditLog:
    """线程安全 JSONL 追加 + 简单轮转。"""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or default_platform_audit_path()
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: Dict[str, Any]) -> Dict[str, Any]:
        rec = dict(event)
        rec.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        if not rec.get("trace_id"):
            rec["trace_id"] = new_trace_id()
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if self._path.exists() and self._path.stat().st_size > _max_bytes():
                    rotated = self._path.with_suffix(self._path.suffix + ".1")
                    try:
                        if rotated.exists():
                            rotated.unlink()
                        self._path.rename(rotated)
                    except OSError as e:
                        logger.warning("platform audit rotate failed: %s", e)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                logger.error("platform audit write failed: %s", e)
        return rec


_singleton: Optional[PlatformAuditLog] = None
_singleton_lock = threading.Lock()


def get_platform_audit_log() -> PlatformAuditLog:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = PlatformAuditLog()
        return _singleton


def reset_platform_audit_for_tests(path: Optional[Path] = None) -> None:
    global _singleton
    with _singleton_lock:
        _singleton = PlatformAuditLog(path) if path is not None else None


def append_platform_audit(
    event_type: str,
    *,
    trace_id: str = "",
    user_id: str = "",
    host_ids: Optional[Sequence[str]] = None,
    host_scope: Optional[Dict[str, Any]] = None,
    command: str = "",
    result_summary: str = "",
    outcome: str = "success",
    duration_ms: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
    path: Optional[Path] = None,
    mirror_mobile: bool = False,
) -> Dict[str, Any]:
    """写入统一平台审计；可选镜像到 mobile_audit（兼容旧大屏）。"""
    try:
        from chibycore.redaction import redact_command_text

        cmd = redact_command_text(command or "", max_len=500) if command else ""
    except Exception:
        cmd = (command or "")[:500]
    event = build_audit_event(
        event_type,
        trace_id=trace_id,
        user_id=user_id,
        host_ids=host_ids,
        host_scope=host_scope,
        command=cmd,
        result_summary=result_summary,
        outcome=outcome,
        duration_ms=duration_ms,
        metadata=metadata,
    )
    log = PlatformAuditLog(path) if path is not None else get_platform_audit_log()
    written = log.append(event)
    if mirror_mobile:
        # 可选镜像到闭源掌上审计：用 importlib，避免 packages 静态 import 闭源包名
        try:
            import importlib

            _mob_audit = importlib.import_module("chiby" + "_mobile.audit")
            _append = getattr(_mob_audit, "append_mobile_audit", None)
            if callable(_append):
                _append(
                    str(written.get("event_type") or event_type),
                    payload={
                        "trace_id": written.get("trace_id"),
                        "user_id": written.get("user_id"),
                        "host_ids": written.get("host_ids"),
                        "host_id": (written.get("host_ids") or [None])[0],
                        "host_scope": written.get("host_scope"),
                        "command": written.get("command"),
                        "result_summary": written.get("result_summary"),
                        "outcome": written.get("outcome"),
                        "duration_ms": written.get("duration_ms"),
                        "metadata": written.get("metadata") or {},
                    },
                )
        except Exception:
            logger.debug("mirror_mobile skipped", exc_info=True)
    return written


def query_platform_audit(
    *,
    limit: int = 50,
    event_type: str = "",
    user_id: str = "",
    host_id: str = "",
    trace_id: str = "",
    q: str = "",
    time_from: str = "",
    time_to: str = "",
    path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    out = path or default_platform_audit_path()
    if not out.is_file():
        return []
    try:
        lines = out.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    window = max(100, min(int(limit) * 10, 5000))
    rows: List[Dict[str, Any]] = []
    for line in lines[-window:]:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    et = (event_type or "").strip().lower()
    uid = (user_id or "").strip()
    hid = (host_id or "").strip()
    tid = (trace_id or "").strip()
    keyword = (q or "").strip().lower()
    t_from = (time_from or "").strip()
    t_to = (time_to or "").strip()

    def _ok(rec: Dict[str, Any]) -> bool:
        if et and str(rec.get("event_type") or "").lower() != et:
            return False
        if uid and str(rec.get("user_id") or "") != uid:
            return False
        if tid and str(rec.get("trace_id") or "") != tid:
            return False
        if hid:
            hosts = rec.get("host_ids") if isinstance(rec.get("host_ids"), list) else []
            if hid not in [str(x) for x in hosts]:
                return False
        ts = str(rec.get("timestamp") or "")
        if t_from and ts < t_from:
            return False
        if t_to and ts > t_to:
            return False
        if keyword:
            blob = json.dumps(rec, ensure_ascii=False).lower()
            if keyword not in blob:
                return False
        return True

    filtered = [r for r in rows if _ok(r)]
    return list(reversed(filtered[-max(1, min(int(limit), 500)) :]))


def query_trace(
    trace_id: str,
    *,
    path: Optional[Path] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    tid = (trace_id or "").strip()
    if not tid:
        return []
    return query_platform_audit(limit=limit, trace_id=tid, path=path)


def event_type_counts(
    *,
    path: Optional[Path] = None,
    limit: int = 2000,
) -> Dict[str, int]:
    rows = query_platform_audit(limit=limit, path=path)
    counts: Dict[str, int] = {}
    for r in rows:
        k = str(r.get("event_type") or "unknown")
        counts[k] = counts.get(k, 0) + 1
    return counts
