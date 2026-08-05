"""静态主机组：持久化 + CRUD（Fleet 范围选机 / 主机管理共用）。"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_lock = threading.RLock()

_VALID_STATUS = frozenset({"online", "offline", "busy", "unknown"})


def normalize_host_status(value: Any) -> str:
    s = str(value or "unknown").strip().lower()
    return s if s in _VALID_STATUS else "unknown"


def normalize_labels(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in raw.items():
        key = str(k or "").strip()
        if not key:
            continue
        out[key] = str(v if v is not None else "").strip()
    return out


def normalize_tags(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    seen = set()
    out: List[str] = []
    for x in raw:
        t = str(x or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _groups_path() -> Path:
    try:
        from chibycore.repo_root import find_repo_root

        return find_repo_root() / "data" / "host_groups.json"
    except Exception:
        return Path.cwd() / "data" / "host_groups.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _empty_store() -> Dict[str, Any]:
    return {"groups": [], "updated_at": time.time()}


def load_groups() -> List[Dict[str, Any]]:
    path = _groups_path()
    with _lock:
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("读取 host_groups.json 失败: %s", exc)
            return []
    items = raw.get("groups") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [normalize_group(x) for x in items if isinstance(x, dict)]


def _save_all(items: List[Dict[str, Any]]) -> None:
    path = _groups_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"groups": items, "updated_at": time.time()}
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def normalize_group(
    raw: Dict[str, Any],
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = dict(existing or {})
    gid = str(raw.get("id") or base.get("id") or "").strip() or (
        "grp_" + uuid.uuid4().hex[:10]
    )
    name = str(raw.get("name") or base.get("name") or "未命名组").strip()[:80] or "未命名组"
    host_ids_raw = raw.get("host_ids") if "host_ids" in raw else base.get("host_ids")
    if not isinstance(host_ids_raw, list):
        host_ids_raw = []
    seen = set()
    host_ids: List[str] = []
    for x in host_ids_raw:
        hid = str(x or "").strip()
        if not hid or hid in seen:
            continue
        seen.add(hid)
        host_ids.append(hid)
    icon = str(raw.get("icon") if "icon" in raw else base.get("icon") or "").strip()[:8]
    color = str(raw.get("color") if "color" in raw else base.get("color") or "").strip()[:32]
    created = str(base.get("created_at") or raw.get("created_at") or _now_iso())
    return {
        "id": gid,
        "name": name,
        "type": "static",
        "host_ids": host_ids,
        "icon": icon,
        "color": color,
        "created_at": created,
        "updated_at": _now_iso(),
    }


def get_group(group_id: str) -> Optional[Dict[str, Any]]:
    gid = str(group_id or "").strip()
    if not gid:
        return None
    for g in load_groups():
        if g.get("id") == gid:
            return g
    return None


def create_group(raw: Dict[str, Any]) -> Dict[str, Any]:
    with _lock:
        items = load_groups()
        g = normalize_group(raw)
        # 避免 id 碰撞
        existing_ids = {x.get("id") for x in items}
        if g["id"] in existing_ids:
            g["id"] = "grp_" + uuid.uuid4().hex[:10]
        items.append(g)
        _save_all(items)
        return g


def update_group(group_id: str, raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    gid = str(group_id or "").strip()
    with _lock:
        items = load_groups()
        for i, old in enumerate(items):
            if old.get("id") != gid:
                continue
            patched = dict(raw)
            patched["id"] = gid
            g = normalize_group(patched, existing=old)
            items[i] = g
            _save_all(items)
            return g
    return None


def delete_group(group_id: str) -> bool:
    gid = str(group_id or "").strip()
    with _lock:
        items = load_groups()
        nxt = [x for x in items if x.get("id") != gid]
        if len(nxt) == len(items):
            return False
        _save_all(nxt)
        return True


def remove_host_from_all_groups(host_id: str) -> int:
    """主机删除时级联剔除；返回修改的组数量。"""
    hid = str(host_id or "").strip()
    if not hid:
        return 0
    changed = 0
    with _lock:
        items = load_groups()
        out: List[Dict[str, Any]] = []
        for g in items:
            ids = list(g.get("host_ids") or [])
            if hid not in ids:
                out.append(g)
                continue
            g2 = dict(g)
            g2["host_ids"] = [x for x in ids if x != hid]
            g2["updated_at"] = _now_iso()
            out.append(g2)
            changed += 1
        if changed:
            _save_all(out)
    return changed


def resolve_group_hosts(
    group_id: str,
    *,
    known_host_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """返回组内有效主机 id；跳过已删除的。"""
    g = get_group(group_id)
    if not g:
        return {"ok": False, "error": "group_not_found", "host_ids": [], "skipped": 0}
    known = set(str(x) for x in (known_host_ids or []) if str(x).strip())
    kept: List[str] = []
    skipped = 0
    for hid in g.get("host_ids") or []:
        h = str(hid)
        if known and h not in known:
            skipped += 1
            continue
        kept.append(h)
    return {
        "ok": True,
        "group": g,
        "host_ids": kept,
        "skipped": skipped,
        "total_listed": len(g.get("host_ids") or []),
    }
