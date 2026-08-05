"""Fleet 定时任务：持久化 + next_run 计算 + 到期扫描（oneshot 跑批由 main 调用）。"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()


def _schedules_path() -> Path:
    try:
        from chibycore.repo_root import find_repo_root

        return find_repo_root() / "data" / "broadcast_schedules.json"
    except Exception:
        return Path.cwd() / "data" / "broadcast_schedules.json"


def _now_local() -> datetime:
    return datetime.now().astimezone()


def _parse_hhmm(value: str) -> tuple[int, int]:
    parts = str(value or "08:00").strip().split(":")
    try:
        h = max(0, min(23, int(parts[0])))
        m = max(0, min(59, int(parts[1]))) if len(parts) > 1 else 0
        return h, m
    except Exception:
        return 8, 0


def compute_next_run_at(
    *,
    freq: str,
    time_hhmm: str,
    weekday: int = 0,
    after: Optional[datetime] = None,
) -> str:
    """返回 ISO8601 next_run_at（本地时区）。weekday: 0=周一 … 6=周日。"""
    base = after or _now_local()
    h, m = _parse_hhmm(time_hhmm)
    freq_n = (freq or "daily").strip().lower()
    candidate = base.replace(hour=h, minute=m, second=0, microsecond=0)
    if freq_n == "weekly":
        # Python: Monday=0 … Sunday=6
        wd = int(weekday) % 7
        days_ahead = (wd - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= base:
            candidate = candidate + timedelta(days=7)
    else:
        if candidate <= base:
            candidate = candidate + timedelta(days=1)
    return candidate.isoformat()


def _empty_store() -> Dict[str, Any]:
    return {"schedules": [], "updated_at": time.time()}


def load_schedules() -> List[Dict[str, Any]]:
    path = _schedules_path()
    with _lock:
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("读取 broadcast_schedules.json 失败: %s", exc)
            return []
    items = raw.get("schedules") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def _save_all(items: List[Dict[str, Any]]) -> None:
    path = _schedules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schedules": items, "updated_at": time.time()}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def normalize_schedule(raw: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = dict(existing or {})
    sid = str(raw.get("id") or base.get("id") or "").strip() or ("sch_" + uuid.uuid4().hex[:12])
    freq = str(raw.get("freq") or base.get("freq") or "daily").strip().lower()
    if freq not in ("daily", "weekly"):
        freq = "daily"
    time_hhmm = str(raw.get("time") or base.get("time") or "08:00").strip() or "08:00"
    try:
        weekday = int(raw.get("weekday") if raw.get("weekday") is not None else base.get("weekday", 0))
    except Exception:
        weekday = 0
    tone = str(raw.get("report_tone") or base.get("report_tone") or "ops").strip().lower()
    fail_policy = str(
        raw.get("fail_policy") or base.get("fail_policy") or "continue"
    ).strip().lower()
    if fail_policy not in ("continue", "all_ok_only"):
        fail_policy = "continue"
    notify_in = raw.get("notify") if isinstance(raw.get("notify"), dict) else (
        base.get("notify") if isinstance(base.get("notify"), dict) else {}
    )
    notify = {
        "feishu": bool(notify_in.get("feishu")),
        "email": bool(notify_in.get("email")),
        "wecom": bool(notify_in.get("wecom")),
    }
    host_ids = raw.get("host_ids")
    if not isinstance(host_ids, list):
        host_ids = list(base.get("host_ids") or [])
    host_ids = [str(x) for x in host_ids if str(x).strip()]
    cmds = raw.get("commands_by_segment")
    if not isinstance(cmds, dict):
        cmds = dict(base.get("commands_by_segment") or {})
    enabled = bool(raw["enabled"]) if "enabled" in raw else bool(base.get("enabled", True))
    name = str(raw.get("name") or base.get("name") or "Fleet 定时巡检").strip()[:120]
    nl_intent = str(raw.get("nl_intent") or base.get("nl_intent") or "").strip()
    next_run = str(raw.get("next_run_at") or "").strip()
    if not next_run or "freq" in raw or "time" in raw or "weekday" in raw:
        next_run = compute_next_run_at(freq=freq, time_hhmm=time_hhmm, weekday=weekday)

    out = {
        "id": sid,
        "name": name,
        "enabled": enabled,
        "freq": freq,
        "time": time_hhmm,
        "weekday": weekday,
        "timezone": "local",
        "host_ids": host_ids,
        "nl_intent": nl_intent,
        "commands_by_segment": {str(k): str(v) for k, v in cmds.items() if str(v).strip()},
        "report_tone": tone,
        "fail_policy": fail_policy,
        "notify": notify,
        "next_run_at": next_run,
        "last_run_at": base.get("last_run_at") or "",
        "last_status": base.get("last_status") or "",
        "last_report_md": base.get("last_report_md") or "",
        "last_notify": base.get("last_notify") or "",
        "created_at": base.get("created_at") or _now_local().isoformat(),
        "updated_at": _now_local().isoformat(),
    }
    return out


def create_schedule(raw: Dict[str, Any]) -> Dict[str, Any]:
    item = normalize_schedule(raw)
    with _lock:
        items = load_schedules()
        items.append(item)
        _save_all(items)
    return item


def update_schedule(schedule_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    with _lock:
        items = load_schedules()
        for i, it in enumerate(items):
            if str(it.get("id")) == schedule_id:
                merged = normalize_schedule(patch, existing=it)
                merged["id"] = schedule_id
                items[i] = merged
                _save_all(items)
                return merged
    return None


def delete_schedule(schedule_id: str) -> bool:
    with _lock:
        items = load_schedules()
        nxt = [x for x in items if str(x.get("id")) != schedule_id]
        if len(nxt) == len(items):
            return False
        _save_all(nxt)
        return True


def get_schedule(schedule_id: str) -> Optional[Dict[str, Any]]:
    for it in load_schedules():
        if str(it.get("id")) == schedule_id:
            return it
    return None


def list_due_schedules(*, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """返回已到期且 enabled 的任务。"""
    cur = now or _now_local()
    due: List[Dict[str, Any]] = []
    for it in load_schedules():
        if not it.get("enabled"):
            continue
        nxt = str(it.get("next_run_at") or "").strip()
        if not nxt:
            continue
        try:
            ts = datetime.fromisoformat(nxt)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=cur.tzinfo)
        except Exception:
            continue
        if ts <= cur:
            due.append(it)
    return due


def mark_schedule_ran(
    schedule_id: str,
    *,
    status: str,
    report_md: str = "",
    notify_note: str = "",
    knowledge_hint: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    with _lock:
        items = load_schedules()
        for i, it in enumerate(items):
            if str(it.get("id")) != schedule_id:
                continue
            it = dict(it)
            it["last_run_at"] = _now_local().isoformat()
            it["last_status"] = (status or "")[:240]
            if report_md:
                it["last_report_md"] = report_md[:20000]
            if notify_note:
                it["last_notify"] = notify_note[:500]
            if knowledge_hint is not None:
                it["last_knowledge_hint"] = knowledge_hint
            it["next_run_at"] = compute_next_run_at(
                freq=str(it.get("freq") or "daily"),
                time_hhmm=str(it.get("time") or "08:00"),
                weekday=int(it.get("weekday") or 0),
                after=_now_local(),
            )
            it["updated_at"] = _now_local().isoformat()
            items[i] = it
            _save_all(items)
            return it
    return None


def list_knowledge_hints() -> List[Dict[str, Any]]:
    """返回带未处理沉淀提示的定时任务摘要。"""
    out: List[Dict[str, Any]] = []
    for it in load_schedules():
        hint = it.get("last_knowledge_hint")
        if not isinstance(hint, dict) or not hint.get("hint"):
            continue
        if hint.get("dismissed"):
            continue
        out.append(
            {
                "schedule_id": it.get("id"),
                "name": it.get("name"),
                "hint": hint,
            }
        )
    return out


def dismiss_knowledge_hint(schedule_id: str) -> bool:
    with _lock:
        items = load_schedules()
        for i, it in enumerate(items):
            if str(it.get("id")) != schedule_id:
                continue
            it = dict(it)
            hint = dict(it.get("last_knowledge_hint") or {})
            if not hint:
                return False
            hint["dismissed"] = True
            it["last_knowledge_hint"] = hint
            items[i] = it
            _save_all(items)
            return True
    return False


def notify_stub(schedule: Dict[str, Any], *, status: str) -> str:
    """飞书/邮件/企微占位：仅写日志说明。"""
    n = schedule.get("notify") or {}
    wanted = [k for k in ("feishu", "email", "wecom") if n.get(k)]
    if not wanted:
        note = "notify: none selected"
    else:
        note = "notify placeholder (not wired): " + ",".join(wanted) + f"; status={status}"
    logger.info(
        "broadcast schedule %s (%s): %s",
        schedule.get("id"),
        schedule.get("name"),
        note,
    )
    return note
