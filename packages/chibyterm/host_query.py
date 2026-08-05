"""主机目录过滤与分页（不改分组模型 / 执行链路）。"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence, Tuple

from chibyterm.host_groups import normalize_host_status, normalize_tags
from chibyterm.models.app import Host


def parse_label_kv(raw: str) -> Tuple[str, str]:
    """解析 ``key=value``；非法格式抛 ValueError。"""
    s = str(raw or "").strip()
    if not s:
        raise ValueError("label 不能为空")
    if "=" not in s:
        raise ValueError("label 须为 key=value 格式")
    key, _, val = s.partition("=")
    key = key.strip()
    if not key:
        raise ValueError("label 的 key 不能为空")
    return key, val.strip()


def _host_field(h: Any, name: str, default: Any = None) -> Any:
    if isinstance(h, dict):
        return h.get(name, default)
    return getattr(h, name, default)


def filter_hosts(
    hosts: Sequence[Any],
    *,
    q: str = "",
    tag: str = "",
    label: str = "",
    status: str = "",
) -> List[Any]:
    """按 q / tag / label / status 过滤；顺序保持输入顺序。"""
    q_norm = str(q or "").strip().lower()
    tag_norm = str(tag or "").strip().lower()
    status_norm = str(status or "").strip().lower()
    label_kv: Optional[Tuple[str, str]] = None
    if str(label or "").strip():
        label_kv = parse_label_kv(label)

    out: List[Any] = []
    for h in hosts:
        if q_norm:
            name = str(_host_field(h, "name", "") or "").lower()
            addr = str(_host_field(h, "host", "") or "").lower()
            hid = str(_host_field(h, "id", "") or "").lower()
            if q_norm not in name and q_norm not in addr and q_norm not in hid:
                continue
        if tag_norm:
            tags = normalize_tags(_host_field(h, "tags", None) or [])
            if tag_norm not in {t.lower() for t in tags}:
                continue
        if label_kv is not None:
            key, want = label_kv
            labels = _host_field(h, "labels", None) or {}
            if not isinstance(labels, dict):
                continue
            if key not in labels or str(labels.get(key)) != want:
                continue
        if status_norm:
            st = normalize_host_status(_host_field(h, "status", None))
            if st != status_norm:
                continue
        out.append(h)
    return out


def parse_id_list(raw: Any, *, limit: int = 500) -> List[str]:
    """解析逗号/分号分隔的 id 列表，去重保序。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(x).strip() for x in raw]
    else:
        text = str(raw).replace(";", ",")
        parts = [x.strip() for x in text.split(",")]
    out: List[str] = []
    seen = set()
    for p in parts:
        if not p or p in seen:
            continue
        seen.add(p)
        out.append(p)
        if len(out) >= limit:
            break
    return out


def prefer_hosts_first(items: Sequence[Any], prefer_ids: Sequence[str]) -> List[Any]:
    """将 prefer_ids 中仍存在于 items 的主机提到最前（分页前调用）。"""
    if not prefer_ids:
        return list(items)
    by_id: dict = {}
    for h in items:
        hid = str(_host_field(h, "id", "") or "").strip()
        if hid:
            by_id[hid] = h
    head: List[Any] = []
    seen = set()
    for hid in prefer_ids:
        h = by_id.get(str(hid).strip())
        if h is None:
            continue
        key = str(_host_field(h, "id", "") or "")
        if key in seen:
            continue
        seen.add(key)
        head.append(h)
    rest = [
        h
        for h in items
        if str(_host_field(h, "id", "") or "").strip() not in seen
    ]
    return head + rest


def clamp_page_size(size: Optional[int], *, default: int = 20, maximum: int = 100) -> int:
    if size is None:
        return default
    try:
        n = int(size)
    except (TypeError, ValueError):
        return default
    if n < 1:
        return 1
    return min(n, maximum)


def paginate_hosts(
    items: Sequence[Any],
    *,
    page: int,
    size: int,
) -> Tuple[List[Any], int, int, int, int]:
    """返回 (page_items, total, page, size, pages)。page 从 1 起。"""
    total = len(items)
    size_n = clamp_page_size(size)
    page_n = max(1, int(page or 1))
    pages = max(1, int(math.ceil(total / size_n))) if total else 0
    if pages and page_n > pages:
        page_n = pages
    start = (page_n - 1) * size_n
    end = start + size_n
    return list(items[start:end]), total, page_n, size_n, pages


def host_list_payload(
    hosts: Sequence[Host],
    *,
    page: Optional[int] = None,
    size: Optional[int] = None,
    q: str = "",
    tag: str = "",
    label: str = "",
    status: str = "",
    prefer_ids: Optional[Sequence[str]] = None,
) -> dict:
    """组装列表响应 dict（供 API / 单测）。

    prefer_ids：过滤后、分页前，将这些 id 提到列表最前（便于 Fleet/分组已选置顶）。
    """
    filtered = filter_hosts(hosts, q=q, tag=tag, label=label, status=status)
    filtered = prefer_hosts_first(filtered, list(prefer_ids or []))
    if page is None:
        return {
            "items": list(filtered),
            "total": len(filtered),
            "page": None,
            "size": None,
            "pages": None,
        }
    page_items, total, page_n, size_n, pages = paginate_hosts(
        filtered, page=page, size=clamp_page_size(size)
    )
    return {
        "items": page_items,
        "total": total,
        "page": page_n,
        "size": size_n,
        "pages": pages,
    }
