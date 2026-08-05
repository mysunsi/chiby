"""AI Ops Assistant — 数据模型。"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ActionType(str, Enum):
    CREATE_USER = "create_user"
    DELETE_USER = "delete_user"
    CHANGE_PASSWORD = "change_password"
    CHECK_USER = "check_user"
    SYSTEM_INFO = "system_info"
    DISK_USAGE = "disk_usage"
    MEMORY_USAGE = "memory_usage"
    CPU_USAGE = "cpu_usage"
    PROCESS_LIST = "process_list"
    SERVICE_STATUS = "service_status"
    SERVICE_START = "service_start"
    SERVICE_STOP = "service_stop"
    SERVICE_RESTART = "service_restart"
    DOCKER_PS = "docker_ps"
    DOCKER_LOGS = "docker_logs"
    PACKAGE_INSTALL = "package_install"
    PACKAGE_REMOVE = "package_remove"
    FILE_READ = "file_read"
    FILE_WRITE = "file_write"
    NETSTAT = "netstat"
    PING = "ping"
    UNKNOWN = "unknown"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    VERIFIED = "verified"
    ROLLED_BACK = "rolled_back"


class ExecutionStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    action: ActionType
    description: str
    command: str
    verify_command: Optional[str] = None
    rollback_command: Optional[str] = None
    status: StepStatus = StepStatus.PENDING
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    verified: bool = False
    verification_output: str = ""
    executed_at: Optional[datetime] = None
    duration_ms: int = 0
    # 扩展: 远程主机标识 (用于多主机场景)
    remote_host: Optional[str] = Field(default=None, description="执行该步骤的目标主机")
    # 扩展: 批次索引 (用于灰度发布)
    batch_index: Optional[int] = Field(default=None, description="所属批次索引")
    # 扩展: 原始命令模板 (用于回滚)
    original_command: Optional[str] = Field(default=None, description="原始命令模板 (回滚时使用)")


class TaskRequest(BaseModel):
    command: str = Field(..., description="自然语言运维指令")
    host: str = Field(default="127.0.0.1")
    ssh_user: str = Field(default="sunsi")
    ssh_password: Optional[str] = Field(default=None, description="SSH密码（会加密存储）")
    # 扩展: 灰度发布支持
    enable_rollout: bool = Field(default=False, description="是否启用灰度发布")
    rollout_percents: List[int] = Field(default=[10, 50, 100], description="灰度百分比")
    gate: Optional["GateConfig"] = Field(default=None, description="Gate 配置")


class TaskResponse(BaseModel):
    task_id: str
    status: str  # pending | running | success | failed | partial
    original_command: str
    parsed_action: ActionType
    parsed_params: Dict
    steps: List[ExecutionStep]
    final_output: str
    error_message: Optional[str] = None
    total_duration_ms: int = 0
    created_at: str
    # 扩展: 灰度发布报告
    rollout_report: Optional["RolloutReport"] = Field(default=None, description="灰度发布报告")


# ══════════════════════════════════════════════════════════════════════════════
# Gate 相关模型
# ══════════════════════════════════════════════════════════════════════════════

class GateKind(str, Enum):
    """Gate 检查类型"""
    HTTP = "http"
    PORT = "port"
    PROCESS = "process"
    PROMQL = "promql"
    CMD = "cmd"


class GateConfig(BaseModel):
    """Gate 配置"""
    kind: GateKind
    # HTTP
    url: Optional[str] = None
    # Port
    port: Optional[int] = None
    host: Optional[str] = None
    # Process
    process_name: Optional[str] = None
    # PromQL
    prom_url: Optional[str] = None
    prom_query: Optional[str] = None
    prom_op: Optional[Literal[">", ">=", "<", "<=", "==", "!="]] = None
    prom_threshold: Optional[float] = None
    # CMD
    cmd: Optional[str] = None
    # 通用
    timeout_s: int = 5


class GateResult(BaseModel):
    """Gate 检查结果"""
    ok: bool
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)
    checked_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    duration_ms: int = 0


# ══════════════════════════════════════════════════════════════════════════════
# 灰度发布相关模型
# ══════════════════════════════════════════════════════════════════════════════

class RolloutPhase(str, Enum):
    """灰度发布阶段"""
    PENDING = "pending"
    EXECUTING = "executing"
    GATE_CHECK = "gate_check"
    GATE_PASSED = "gate_passed"
    GATE_FAILED = "gate_failed"
    ROLLBACK = "rollback"
    COMPLETED = "completed"
    FAILED = "failed"


class BatchReport(BaseModel):
    """单批次报告"""
    batch_index: int = Field(..., description="批次索引 (从 1 开始)")
    batch_percent: int = Field(..., description="该批次对应百分比")
    hosts: List[str] = Field(..., description="该批次包含的主机")
    steps_results: List[Dict[str, Any]] = Field(default_factory=list, description="各步骤执行结果")
    success: bool = Field(..., description="批次是否成功")
    gate_result: Optional[GateResult] = Field(default=None, description="Gate 检查结果")
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration_s: float = 0.0


class RolloutProgress(BaseModel):
    """实时进度推送"""
    phase: RolloutPhase = Field(..., description="当前阶段")
    batch_index: int = Field(..., description="当前批次索引")
    batch_total: int = Field(..., description="总批次数")
    current_hosts: List[str] = Field(default_factory=list, description="当前批次主机")
    gate_ok: Optional[bool] = Field(default=None, description="Gate 检查结果")
    message: str = Field(default="", description="状态消息")
    batch_duration_s: Optional[float] = Field(default=None, description="当前批次耗时")
    total_duration_s: Optional[float] = Field(default=None, description="总耗时")


class RolloutRequest(BaseModel):
    """灰度发布请求"""
    command: str = Field(..., description="自然语言指令")
    hosts: List[str] = Field(..., description="目标主机列表")
    ssh_user: str = Field(default="root")
    ssh_password: Optional[str] = None
    gate: Optional[GateConfig] = None
    percents: List[int] = Field(default=[10, 50, 100])
    auto_rollback: bool = Field(default=True)
    dry_run: bool = Field(default=False)
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    verify_command: Optional[str] = None
    rollback_command: Optional[str] = None


class RolloutReport(BaseModel):
    """完整灰度发布报告"""
    rollout_id: str = Field(default_factory=lambda: f"rollout-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:8]}")
    user_command: str
    hosts: List[str]
    percents: List[int]
    gate_config: Optional[GateConfig] = None
    batches: List[BatchReport] = Field(default_factory=list)
    rollback_report: Optional["RolloutReport"] = None
    success: bool
    auto_rollback: bool = True
    total_duration_s: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None


class RolloutPlan(BaseModel):
    """灰度发布计划预览"""
    hosts: List[str]
    batches: List[Dict[str, Any]] = Field(default_factory=list, description="批次划分预览")
    gate: Optional[GateConfig] = None
    auto_rollback: bool = True
    estimated_duration_s: float = 0.0
