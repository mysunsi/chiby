"""CDU 注册表（按助手声明单元）。"""
from __future__ import annotations

from typing import Dict, List, Optional

from chibyterm.context_units.types import ContextUnitSpec

ASSISTANT_CHIBY_MOBILE = "chiby_mobile"

_SPECS: Dict[str, Dict[str, ContextUnitSpec]] = {
    ASSISTANT_CHIBY_MOBILE: {
        "host_targets": ContextUnitSpec(
            unit_id="host_targets",
            assistant_id=ASSISTANT_CHIBY_MOBILE,
            title="目标主机",
            description="当前选中的运维目标主机（上下文单元，非工具）。",
            ui_slot="chrome_top_left",
            storage="both",
            user_scoped=True,
            scope="user",
            required_for_tools=("host_readonly", "host_write", "host_command"),
        ),
    }
}


def list_unit_specs(assistant_id: str = ASSISTANT_CHIBY_MOBILE) -> List[ContextUnitSpec]:
    aid = (assistant_id or ASSISTANT_CHIBY_MOBILE).strip() or ASSISTANT_CHIBY_MOBILE
    return list((_SPECS.get(aid) or {}).values())


def get_unit_spec(
    unit_id: str, *, assistant_id: str = ASSISTANT_CHIBY_MOBILE
) -> Optional[ContextUnitSpec]:
    aid = (assistant_id or ASSISTANT_CHIBY_MOBILE).strip() or ASSISTANT_CHIBY_MOBILE
    uid = (unit_id or "").strip()
    return (_SPECS.get(aid) or {}).get(uid)
