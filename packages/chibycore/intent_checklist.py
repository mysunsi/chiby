"""意图级闭环：Intent Checklist（L0）— 子目标清单 + 逐项执行直至意图完成。

命令级闭环（run_closure_retry_loop / goal_resume）负责「单项命令修好并复验」；
本模块负责「用户意图拆成多项 → 逐项完成 → 进度更新」。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from chibycore.closure_retry_runner import ClosureRunResult, run_closure_retry_loop
from chibycore.executor_contract import ExecResult

ExecuteFn = Callable[[str], ExecResult]
GatewayAllowFn = Any  # 与 closure_retry_runner 兼容
OnItemProgressFn = Callable[["IntentChecklist", "IntentCheckItem"], None]

_ITEM_OK_REASONS = frozenset({"success_initial", "success_after_fix"})

# 复合命令拆分：按 && 分段（保留常见查询场景；不拆 || 以免短路语义丢失过多）
_AND_SPLIT_RE = re.compile(r"\s*&&\s*")


@dataclass
class IntentCheckItem:
    id: str
    description: str
    command: str
    status: str = "pending"  # pending | running | completed | failed | skipped
    result_summary: str = ""
    stop_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IntentChecklist:
    intent: str
    items: List[IntentCheckItem] = field(default_factory=list)
    status: str = "pending"  # pending | running | partial | completed | failed

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def completed_count(self) -> int:
        return sum(1 for it in self.items if it.status == "completed")

    @property
    def failed_count(self) -> int:
        return sum(1 for it in self.items if it.status == "failed")

    def is_completed(self) -> bool:
        return self.total > 0 and self.completed_count == self.total

    def get_next_pending(self) -> Optional[IntentCheckItem]:
        for it in self.items:
            if it.status == "pending":
                return it
        return None

    def update_progress(self) -> None:
        if not self.items:
            self.status = "failed"
            return
        if self.is_completed():
            self.status = "completed"
            return
        if any(it.status == "running" for it in self.items):
            self.status = "running"
            return
        if self.failed_count and self.completed_count:
            self.status = "partial"
            return
        if self.failed_count and not self.completed_count:
            self.status = "failed"
            return
        if self.completed_count:
            self.status = "running"
            return
        self.status = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "status": self.status,
            "completed": self.completed_count,
            "total": self.total,
            "items": [it.to_dict() for it in self.items],
        }


def _short_desc_from_command(cmd: str, index: int) -> str:
    c = (cmd or "").strip()
    # -T / -t / -V 大小写敏感（nginx 语义不同）
    if re.search(r"(?<![-\w])nginx\s+-T\b", c):
        return "打印 nginx 完整配置"
    if re.search(r"(?<![-\w])nginx\s+-V\b", c):
        return "获取 nginx 编译参数"
    if re.search(r"(?<![-\w])nginx\s+-t\b", c):
        return "检查 nginx 配置语法"
    if len(c) <= 48:
        return c
    return f"步骤 {index + 1}: {c[:40]}…"


def maybe_split_compound_command(cmd: str) -> List[str]:
    """将 ``a && b && c`` 拆成多条；单条或无法安全拆分则原样返回。"""
    text = (cmd or "").strip()
    if not text or "&&" not in text:
        return [text] if text else []
    # 粗过滤：引号内含 && 时不拆（避免破坏 echo "a && b"）
    if text.count('"') % 2 == 1 or text.count("'") % 2 == 1:
        return [text]
    parts = [p.strip() for p in _AND_SPLIT_RE.split(text) if p.strip()]
    return parts if len(parts) >= 2 else [text]


def checklist_from_plan_steps(
    intent: str,
    steps: Sequence[Dict[str, Any]],
    *,
    split_compound: bool = True,
) -> IntentChecklist:
    """由 PlanRuntime.steps 映射意图清单；可选拆分单步复合命令。"""
    items: List[IntentCheckItem] = []
    n = 0
    for st in steps or []:
        raw_cmd = str(st.get("command") or "").strip()
        if not raw_cmd:
            continue
        title = str(st.get("title") or "").strip()
        cmds = maybe_split_compound_command(raw_cmd) if split_compound else [raw_cmd]
        # 仅当「整计划只有一步且被拆开」或「该步本身是复合」时用拆分结果
        if len(cmds) == 1:
            n += 1
            items.append(
                IntentCheckItem(
                    id=f"c{n}",
                    description=title or _short_desc_from_command(raw_cmd, n - 1),
                    command=raw_cmd,
                )
            )
            continue
        for part in cmds:
            n += 1
            items.append(
                IntentCheckItem(
                    id=f"c{n}",
                    description=_short_desc_from_command(part, n - 1),
                    command=part,
                )
            )
    return IntentChecklist(intent=(intent or "").strip() or "（未命名意图）", items=items)


def _result_summary(result: ClosureRunResult) -> str:
    cp = result.final_payload
    if not cp:
        return (result.stop_reason or "")[:200]
    out = (cp.stdout or "").strip()
    err = (cp.stderr or "").strip()
    if out:
        return out[-400:]
    if err:
        return err[-400:]
    return f"exit={cp.exit_code} · {result.stop_reason}"


def item_succeeded(result: ClosureRunResult) -> bool:
    """项成功：闭环 ok 且 stop_reason 为意图可接受的成功类。"""
    if not result or not result.ok:
        return False
    return (result.stop_reason or "") in _ITEM_OK_REASONS


def run_intent_checklist(
    *,
    checklist: IntentChecklist,
    execute: ExecuteFn,
    gateway_allow: GatewayAllowFn,
    shell_profile: str = "unix",
    session_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    max_fix_attempts: int = 3,
    success_mode: str = "exit_code",
    verify_original_after_fix: bool = True,
    on_item_progress: Optional[OnItemProgressFn] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
    stop_on_item_failure: bool = True,
) -> IntentChecklist:
    """逐项执行意图清单；每项内部走命令级闭环（含 goal_resume）。"""
    if not checklist.items:
        checklist.status = "failed"
        return checklist

    checklist.status = "running"
    if on_item_progress:
        on_item_progress(checklist, checklist.items[0])

    for item in checklist.items:
        if cancel_check and cancel_check():
            item.status = "skipped"
            checklist.update_progress()
            if on_item_progress:
                on_item_progress(checklist, item)
            break

        item.status = "running"
        checklist.update_progress()
        if on_item_progress:
            on_item_progress(checklist, item)

        hint = checklist.intent
        if item.description:
            hint = f"{checklist.intent} · 当前子目标：{item.description}"

        result = run_closure_retry_loop(
            trace_id=f"ic_{item.id}",
            initial_command=item.command,
            execute=execute,
            gateway_allow=gateway_allow,
            shell_profile=shell_profile,
            distro_family=distro_family,
            pkg_manager=pkg_manager,
            nl_intent_hint=hint,
            session_id=session_id,
            plan_id=plan_id,
            max_fix_attempts=max_fix_attempts,
            success_mode=success_mode,
            verify_original_after_fix=verify_original_after_fix,
            archive_kb=False,
            cancel_check=cancel_check,
        )
        item.stop_reason = result.stop_reason or ""
        item.result_summary = _result_summary(result)
        if item_succeeded(result):
            item.status = "completed"
        else:
            item.status = "failed"
        checklist.update_progress()
        if on_item_progress:
            on_item_progress(checklist, item)
        if item.status == "failed" and stop_on_item_failure:
            # 后续标为 skipped
            for rest in checklist.items:
                if rest.status == "pending":
                    rest.status = "skipped"
            checklist.update_progress()
            if on_item_progress:
                on_item_progress(checklist, item)
            break

    checklist.update_progress()
    return checklist
