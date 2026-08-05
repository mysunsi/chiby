"""从 data/custom_task_chains.json 加载用户自定义任务链并注册到 TASK_CHAINS。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from chibycore.chains import TASK_CHAINS, ChainStepConfig, TaskChain
from chibycore.schemas import ActionType

logger = logging.getLogger(__name__)


def _parse_action(name: str) -> ActionType:
    n = (name or "").strip()
    if not n:
        return ActionType.SYSTEM_INFO
    try:
        return ActionType(n)
    except ValueError:
        pass
    up = n.upper()
    if up in ActionType.__members__:
        return ActionType[up]
    return ActionType.SYSTEM_INFO


def load_custom_task_chains_file(path: Path) -> Dict[str, TaskChain]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("custom_task_chains 解析失败: %s", e)
        return {}
    out: Dict[str, TaskChain] = {}
    for item in raw.get("chains") or []:
        cid = (item.get("id") or "").strip()
        if not cid or cid in TASK_CHAINS:
            continue
        name = item.get("name") or cid
        kws = item.get("intent_keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        desc = item.get("description") or ""
        steps_in: List[Dict[str, Any]] = item.get("steps") or []
        steps: List[ChainStepConfig] = []
        for s in steps_in:
            act = _parse_action(str(s.get("action", "SYSTEM_INFO")))
            pg = s.get("parallel_group")
            steps.append(
                ChainStepConfig(
                    action=act,
                    description=str(s.get("description") or act.value),
                    depends_on=list(s.get("depends_on") or []),
                    parallel_group=str(pg) if pg else None,
                    continue_on_fail=bool(s.get("continue_on_fail", False)),
                    timeout=int(s.get("timeout") or 60),
                )
            )
        if not steps:
            continue
        out[cid] = TaskChain(
            name=name,
            intent_keywords=[str(x) for x in kws],
            description=desc,
            steps=steps,
            requires_approval=bool(item.get("requires_approval", True)),
        )
    return out


def register_custom_chains(project_root: Path) -> int:
    """将自定义链合并进 TASK_CHAINS，返回新增条数。"""
    path = project_root / "data" / "custom_task_chains.json"
    extra = load_custom_task_chains_file(path)
    n = 0
    for cid, chain in extra.items():
        TASK_CHAINS[cid] = chain
        n += 1
    if n:
        logger.info("已加载 %s 条自定义任务链: %s", n, list(extra.keys()))
    return n
