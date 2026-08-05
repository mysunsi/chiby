"""CDU 类型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

StorageMode = Literal["local", "server", "both"]
UnitScope = Literal["user", "conversation"]


@dataclass(frozen=True)
class ContextUnitSpec:
    unit_id: str
    assistant_id: str
    title: str
    description: str = ""
    ui_slot: str = ""
    storage: StorageMode = "both"
    user_scoped: bool = True
    #: user = 跨会话共享（HostTargets 定稿）；conversation = 键含 session（预留）
    scope: UnitScope = "user"
    required_for_tools: tuple[str, ...] = ()


@dataclass
class UnitValue:
    """单元当前值（不含凭据）。"""

    unit_id: str
    assistant_id: str
    user_key: str
    data: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = 0.0

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "assistant_id": self.assistant_id,
            "user_key": self.user_key,
            "data": dict(self.data),
            "updated_at": float(self.updated_at or 0.0),
        }


def user_key_for(external_user_id: Optional[str]) -> str:
    uid = (external_user_id or "").strip()
    return uid if uid else "anon"
