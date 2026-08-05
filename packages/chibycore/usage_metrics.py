"""本地匿名使用指标（不上传、不入库 Git；供 Pro 阈值决策）。"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _repo_root() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()


def default_metrics_path() -> Path:
    p = os.environ.get("USAGE_METRICS_FILE", "").strip()
    if p:
        return Path(p)
    return _repo_root() / "data" / "usage" / "metrics.json"


def _count_platform_events(event_type: str, *, days: int = 30) -> int:
    try:
        from chibycore.platform_audit import default_platform_audit_path

        path = default_platform_audit_path()
        if not path.is_file():
            return 0
        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=max(1, days))
        ).isoformat()
        n = 0
        for line in path.read_text(encoding="utf-8").splitlines()[-5000:]:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(rec.get("event_type") or "") != event_type:
                continue
            ts = str(rec.get("timestamp") or "")
            if ts and ts >= cutoff:
                n += 1
        return n
    except Exception:
        return 0


def collect_anonymous_metrics() -> Dict[str, Any]:
    """收集匿名使用指标快照。"""
    total_hosts = 0
    total_groups = 0
    total_schedules = 0
    try:
        hp = _repo_root() / "data" / "hosts.json"
        if hp.is_file():
            data = json.loads(hp.read_text(encoding="utf-8"))
            if isinstance(data, list):
                total_hosts = len(data)
            elif isinstance(data, dict):
                items = data.get("hosts")
                total_hosts = len(items) if isinstance(items, list) else len(data)
    except Exception:
        total_hosts = 0
    try:
        from chibyterm import host_groups as hg

        total_groups = len(hg.load_groups() or [])
    except Exception:
        total_groups = 0
    try:
        from chibyterm.broadcast_schedule import load_schedules

        total_schedules = len(load_schedules() or [])
    except Exception:
        try:
            sp = _repo_root() / "data" / "broadcast_schedules.json"
            if sp.is_file():
                raw = json.loads(sp.read_text(encoding="utf-8"))
                items = raw if isinstance(raw, list) else (raw.get("schedules") or [])
                total_schedules = len(items)
        except Exception:
            total_schedules = 0

    return {
        "date": datetime.now(timezone.utc).isoformat(),
        "total_hosts": int(total_hosts),
        "total_groups": int(total_groups),
        "total_scheduled_jobs": int(total_schedules),
        "fleet_executions_last_30d": _count_platform_events("fleet_execute", days=30),
        "diagnosis_last_30d": _count_platform_events("ai_diagnosis", days=30),
    }


def refresh_usage_metrics(*, path: Optional[Path] = None) -> Dict[str, Any]:
    """写入 ``data/usage/metrics.json``（覆盖为最新快照）。"""
    snap = collect_anonymous_metrics()
    out = path or default_metrics_path()
    with _lock:
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(snap, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("usage metrics write failed: %s", e)
    return snap
