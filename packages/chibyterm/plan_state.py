"""终端 AI 计划模式：自然语言 → 命令集 → 用户批准 → 分步/批量执行 → step_ok。"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def new_plan_id() -> str:
    return "plt_" + uuid.uuid4().hex[:12]


@dataclass
class PlanRuntime:
    """单个会话上当前活跃的一条执行计划（内存态，会话关闭即丢弃）。"""

    plan_id: str
    explanation: str
    source: str  # llm | chain
    steps: List[Dict[str, Any]]
    chain_id: Optional[str] = None
    phase: str = "pending_approval"  # pending_approval | running | awaiting_step_ok | awaiting_danger_confirm | done | aborted
    current_index: int = 0
    style: str = "gated"  # gated | batch
    danger_line: Optional[str] = None
    #: 用户原始意图（自然语言）；意图级闭环进度用
    intent: str = ""
    #: IntentChecklist.to_dict() 快照（可选）
    checklist: Optional[Dict[str, Any]] = None

    def total_steps(self) -> int:
        return len(self.steps)

    def current_step(self) -> Optional[Dict[str, Any]]:
        if 0 <= self.current_index < len(self.steps):
            return self.steps[self.current_index]
        return None
