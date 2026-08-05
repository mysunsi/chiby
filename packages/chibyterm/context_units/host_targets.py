"""HostTargets CDU：当前选中主机（非工具）。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from chibyterm.context_units.registry import ASSISTANT_CHIBY_MOBILE
from chibyterm.context_units.store import ContextUnitStore, default_unit_store
from chibyterm.context_units.types import UnitValue

if TYPE_CHECKING:
    from chibyterm.models.session import ConversationState

UNIT_ID_HOST_TARGETS = "host_targets"


@dataclass
class HostScopeView:
    """展示 / 提示词用的主机范围视图（非独立会话实体）。"""

    host_ids: List[str]
    group_id: str = ""
    group_name: str = ""

    @property
    def host_count(self) -> int:
        return len(self.host_ids)

    @property
    def display_name(self) -> str:
        n = self.host_count
        gname = (self.group_name or "").strip()
        if gname:
            return f"{gname}（{n}台）"
        if n <= 0:
            return "未选择主机"
        return f"已选 {n} 台主机"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "host_ids": list(self.host_ids),
            "group_id": self.group_id or "",
            "group_name": self.group_name or "",
            "display_name": self.display_name,
            "host_count": self.host_count,
        }


def host_scope_from_state(st: "ConversationState") -> HostScopeView:
    ids = [str(x).strip() for x in (getattr(st, "ui_host_ids", None) or []) if str(x).strip()]
    return HostScopeView(
        host_ids=ids,
        group_id=str(getattr(st, "ui_host_group_id", None) or "").strip(),
        group_name=str(getattr(st, "ui_host_group_name", None) or "").strip(),
    )


def host_targets_from_state(st: "ConversationState") -> Dict[str, Any]:
    ids = [str(x).strip() for x in (getattr(st, "ui_host_ids", None) or []) if str(x).strip()]
    return {
        "host_ids": ids,
        "bound_host_id": str(getattr(st, "bound_host_id", None) or "") or (
            ids[0] if ids else ""
        ),
        "group_id": str(getattr(st, "ui_host_group_id", None) or "").strip(),
        "group_name": str(getattr(st, "ui_host_group_name", None) or "").strip(),
        "updated_at": float(getattr(st, "updated_at", None) or 0.0),
    }


def apply_host_targets_to_state(
    st: "ConversationState",
    *,
    host_ids: Sequence[str],
    bound_host_id: Optional[str] = None,
    group_id: Optional[str] = None,
    group_name: Optional[str] = None,
) -> None:
    accepted = [str(x).strip() for x in host_ids if str(x).strip()]
    st.ui_host_ids = list(accepted)
    if bound_host_id and bound_host_id in accepted:
        st.bound_host_id = bound_host_id
    elif accepted:
        st.bound_host_id = accepted[0]
    else:
        st.bound_host_id = None
    # group 元数据：显式传入时更新；清空主机时一并清空组
    if not accepted:
        st.ui_host_group_id = ""
        st.ui_host_group_name = ""
    else:
        if group_id is not None:
            st.ui_host_group_id = str(group_id or "").strip()
        if group_name is not None:
            st.ui_host_group_name = str(group_name or "").strip()[:80]
    st.updated_at = time.time()


def resolve_host_targets(
    candidate_ids: Sequence[str],
    *,
    known_ids: Sequence[str],
    allowed_ids: Optional[set],
) -> Dict[str, Any]:
    """ACL + 可见表过滤；返回 host_ids / bound_host_id / skipped。

    空选合法：host_ids=[] 时由编排返回 need_host，禁止静默挑机
   （见 docs/context-data-unit-architecture.md §4.1）。
    """
    try:
        import importlib

        _acl = importlib.import_module("chiby_mobile.acl")
        filter_host_ids_by_acl = _acl.filter_host_ids_by_acl
    except ImportError:
        def filter_host_ids_by_acl(host_ids, allowed):  # type: ignore[misc]
            if allowed is None:
                return [], [(h, "unauthorized") for h in host_ids]
            accepted, skipped = [], []
            for h in host_ids:
                if h in allowed:
                    accepted.append(h)
                else:
                    skipped.append((h, "acl_denied"))
            return accepted, skipped

    known = {str(x).strip() for x in known_ids if str(x).strip()}
    raw_ids = [str(x).strip() for x in (candidate_ids or []) if str(x).strip()]
    if allowed_ids is None:
        return {
            "ok": False,
            "error": "未授权用户",
            "host_ids": [],
            "bound_host_id": "",
            "skipped": [],
        }
    accepted, skipped = filter_host_ids_by_acl(raw_ids, allowed_ids)
    accepted = [h for h in accepted if h in known]
    for hid in raw_ids:
        if hid not in known and (hid, "unknown") not in skipped:
            skipped.append((hid, "unknown"))
    bound = accepted[0] if accepted else ""
    return {
        "ok": True,
        "host_ids": accepted,
        "bound_host_id": bound,
        "skipped": [{"host_id": a, "reason": b} for a, b in skipped],
    }


def save_host_targets(
    *,
    host_ids: Sequence[str],
    external_user_id: str,
    assistant_id: str = ASSISTANT_CHIBY_MOBILE,
    store: Optional[ContextUnitStore] = None,
    known_ids: Optional[Sequence[str]] = None,
    allowed_ids: Optional[set] = None,
    group_id: Optional[str] = None,
    group_name: Optional[str] = None,
) -> Dict[str, Any]:
    """校验并写入 UnitStore；可选带 ACL（known/allowed 均给时）。"""
    ids = [str(x).strip() for x in host_ids if str(x).strip()]
    if known_ids is not None and allowed_ids is not None:
        resolved = resolve_host_targets(ids, known_ids=known_ids, allowed_ids=allowed_ids)
        if not resolved.get("ok"):
            return resolved
        ids = list(resolved["host_ids"])
        bound = str(resolved.get("bound_host_id") or "")
        skipped = resolved.get("skipped") or []
    else:
        bound = ids[0] if ids else ""
        skipped = []
    gid = str(group_id or "").strip() if ids else ""
    gname = str(group_name or "").strip()[:80] if ids else ""
    st_store = store or default_unit_store()
    value = st_store.save(
        assistant_id=assistant_id or ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id=external_user_id,
        data={
            "host_ids": ids,
            "bound_host_id": bound,
            "group_id": gid,
            "group_name": gname,
        },
    )
    return {
        "ok": True,
        "host_ids": ids,
        "bound_host_id": bound,
        "group_id": gid,
        "group_name": gname,
        "skipped": skipped,
        "unit": value.to_public_dict(),
        "updated_at": value.updated_at,
    }


def load_host_targets(
    *,
    external_user_id: str,
    assistant_id: str = ASSISTANT_CHIBY_MOBILE,
    store: Optional[ContextUnitStore] = None,
) -> Optional[UnitValue]:
    st_store = store or default_unit_store()
    return st_store.load(
        assistant_id=assistant_id or ASSISTANT_CHIBY_MOBILE,
        unit_id=UNIT_ID_HOST_TARGETS,
        external_user_id=external_user_id,
    )


def hydrate_host_targets_into_state(
    st: "ConversationState",
    *,
    external_user_id: str,
    assistant_id: str = ASSISTANT_CHIBY_MOBILE,
    store: Optional[ContextUnitStore] = None,
    prefer_server: bool = True,
) -> Dict[str, Any]:
    """启动/回合前：UnitStore → 会话镜像字段。

    prefer_server=True：有服务端数据时覆盖会话空选或冲突。
    """
    loaded = load_host_targets(
        external_user_id=external_user_id,
        assistant_id=assistant_id,
        store=store,
    )
    session_ids = [
        str(x).strip() for x in (getattr(st, "ui_host_ids", None) or []) if str(x).strip()
    ]
    if loaded is None:
        return {"ok": True, "source": "session", "host_ids": session_ids}
    server_ids = [
        str(x).strip()
        for x in (loaded.data.get("host_ids") or [])
        if str(x).strip()
    ]
    if prefer_server and (server_ids or not session_ids):
        apply_host_targets_to_state(
            st,
            host_ids=server_ids,
            bound_host_id=str(loaded.data.get("bound_host_id") or "") or None,
            group_id=str(loaded.data.get("group_id") or ""),
            group_name=str(loaded.data.get("group_name") or ""),
        )
        return {
            "ok": True,
            "source": "server",
            "host_ids": list(st.ui_host_ids or []),
            "group_id": getattr(st, "ui_host_group_id", "") or "",
            "group_name": getattr(st, "ui_host_group_name", "") or "",
            "updated_at": loaded.updated_at,
        }
    return {"ok": True, "source": "session", "host_ids": session_ids}
