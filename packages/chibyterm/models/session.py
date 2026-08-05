"""会话状态契约（持久化 / 编排共用）。

从 ``terminal.mobile.orchestrator`` 下沉，避免 session_store 反向依赖编排器。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from chibyterm.models.exec import ExecResult


@dataclass
class PendingPermission:
    permission_id: str
    host_id: str
    command: str
    created_at: float
    external_user_id: str
    source: str = "rules"  # rules | hermes | agent | advanced_mutate | advanced_continue | a2_continue
    require_typed_confirm: bool = False
    require_otp: bool = False
    risk_level: str = ""
    operation_type: str = ""


@dataclass
class ConversationState:
    conversation_id: str
    bound_host_id: Optional[str] = None
    #: IM 顶栏所选目标（1 台或多台）；多机 NL/Job 优先用此列表
    ui_host_ids: List[str] = field(default_factory=list)
    #: 来自 Fleet 静态组的元数据（展示 / 提示词；非强制过滤）
    ui_host_group_id: str = ""
    ui_host_group_name: str = ""
    pending: Optional[PendingPermission] = None
    last_exec: Optional[ExecResult] = None
    last_planner: str = ""
    last_bot_offer: str = ""
    #: 运维模式用户原问（确认执行后回灌 LLM 梳理用）
    last_user_text: str = ""
    #: 上一轮用户原文（写入 last_user_text 前保留，供 Hermes 跨轮续接）
    prev_user_text: str = ""
    awaiting_followup: bool = False
    pending_plan_commands: List[str] = field(default_factory=list)
    #: A2 确认卡挂起的结构化远端工具（含写文件 content；批准后走 execute_remote_tool_call）
    pending_remote_tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    agent_mode: str = "efficient"  # efficient | intelligent | omnipotent（兼容旧 ops/advanced/code）
    #: 编程能力挂接（ADR-0004：非独立模式）
    coding_capability: bool = False
    # 高级闭环检查点续跑：{host_id, conn_type, commands, seen, rounds_done, preface}
    adv_closure_resume: Optional[Dict[str, Any]] = None
    #: 全能型 A2 检查点：{host_id, conn_type, rounds_done, preface, last_results, reason, turn_id}
    a2_closure_resume: Optional[Dict[str, Any]] = None
    # 高级模式多主机：Hermes 分析轮暂存，出 OPS_JOB 或只读 OPS_PLAN 后扇出
    pending_multi_job: Optional[Dict[str, Any]] = None
    # 跨轮诊断焦点（重大发现 / 高频异常短句），供对比类追问锚定
    diag_focus: List[str] = field(default_factory=list)
    #: 当前用户回合 ID（plan→exec→feedback 同账；检查点续跑复用）
    current_turn_id: str = ""
    #: 上一回合短摘要（落盘，供冷启动续问）
    last_turn_summary: str = ""
    #: 本轮聊天附件 id（Agent 分析 / remote_write attachment_id）
    pending_chat_attachment_id: str = ""
    #: 最近一次规划/续接实际锚定的主机（顶栏先 PUT 改绑定时，发消息时 prev==cur，靠此检测切机）
    last_hermes_host_id: str = ""
    #: 顶栏选机已变、待本回合 flush ACP 并清空跨机续接
    host_switch_pending: bool = False
    #: last_bot_offer / diag_focus / last_turn_summary 归属的主机（切机校验用）
    continuity_host_id: str = ""
    updated_at: float = field(default_factory=time.time)
