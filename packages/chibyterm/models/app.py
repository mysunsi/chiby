"""终端应用数据模型。"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from chibyterm.distro_profile import DistroProfile


# ─── 连接类型 ────────────────────────────────────────────────────────────────

class ConnType(str, Enum):
    LOCAL = "local"
    SSH = "ssh"
    WINRM = "winrm"


class Host(BaseModel):
    """主机节点。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    host: str              # IP 或域名
    port: int = 22         # SSH 端口（conn_type=ssh 时）
    username: str
    password: Optional[str] = None        # 加密存储
    conn_type: ConnType = ConnType.SSH
    description: str = ""
    tags: List[str] = []
    # 键值标签（env/role 等）；旧 hosts.json 缺字段时默认为 {}
    labels: Dict[str, str] = Field(default_factory=dict)
    # online | offline | busy | unknown；本期可不做心跳，测连成功可标 online
    status: str = "unknown"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_used: Optional[datetime] = None
    is_active: bool = True
    # WinRM（conn_type=winrm 时使用；SSH 主机可保留默认）
    winrm_port: int = 5985
    winrm_use_ssl: bool = False
    winrm_transport: str = "ntlm"  # ntlm | credssp | basic | certificate | kerberos | ssl
    winrm_server_cert_validation: str = "ignore"  # validate | ignore（内网测试常用 ignore）
    # WinRM 终端模式：interactive=WinRS 流式 Shell（PSReadLine）；psrp_line=每次 Enter 执行一行 PSRP（与同事 Demo 类似，延迟低）
    winrm_shell_mode: str = "interactive"
    # SSH 密钥登录（与 password 二选一或并存；路径为服务端可读的文件）
    ssh_private_key_path: Optional[str] = None
    ssh_private_key_passphrase: Optional[str] = None
    # Linux 发行版命令族指纹（SSH 探测或手改；见 docs/linux-distro-command-profile-design.md）
    distro_profile: Optional[DistroProfile] = None


class HostCreate(BaseModel):
    name: str
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    description: str = ""
    tags: List[str] = []
    labels: Dict[str, str] = Field(default_factory=dict)
    status: str = "unknown"
    conn_type: str = "ssh"  # ssh | winrm
    winrm_port: int = 5985
    winrm_use_ssl: bool = False
    winrm_transport: str = "ntlm"
    winrm_server_cert_validation: str = "ignore"
    winrm_shell_mode: str = "interactive"
    ssh_private_key_path: Optional[str] = None
    ssh_private_key_passphrase: Optional[str] = None
    distro_profile: Optional[DistroProfile] = None


class HostUpdate(BaseModel):
    """更新主机：仅提交需要修改的字段（未出现的字段保持不变）。"""
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    labels: Optional[Dict[str, str]] = None
    status: Optional[str] = None
    conn_type: Optional[str] = None
    winrm_port: Optional[int] = None
    winrm_use_ssl: Optional[bool] = None
    winrm_transport: Optional[str] = None
    winrm_server_cert_validation: Optional[str] = None
    winrm_shell_mode: Optional[str] = None
    ssh_private_key_path: Optional[str] = None
    ssh_private_key_passphrase: Optional[str] = None
    distro_profile: Optional[DistroProfile] = None


class HostTestConnectionRequest(BaseModel):
    """测试主机连通性（可不落库；编辑时可带 host_id 复用已存凭据）。"""
    host: str
    username: str
    name: str = ""
    port: int = 22
    password: Optional[str] = None
    conn_type: str = "ssh"
    winrm_port: int = 5985
    winrm_use_ssl: bool = False
    winrm_transport: str = "ntlm"
    winrm_server_cert_validation: str = "ignore"
    ssh_private_key_path: Optional[str] = None
    ssh_private_key_passphrase: Optional[str] = None
    host_id: Optional[str] = None


class HostTestConnectionResponse(BaseModel):
    ok: bool
    message: str = ""
    detail: str = ""
    latency_ms: Optional[float] = None


class HostListResponse(BaseModel):
    """主机列表（支持过滤；可选分页）。不传 page 时为全量兼容形态。"""

    items: List[Host]
    total: int
    page: Optional[int] = None
    size: Optional[int] = None
    pages: Optional[int] = None


class SessionCreate(BaseModel):
    """创建会话的请求体（JSON body）"""
    host_id: Optional[str] = None
    title: str = "新终端"
    conn_type: str = "local"   # "local" | "ssh" | "winrm"


class SessionUpdate(BaseModel):
    """更新会话（部分字段）。"""
    target_os: Optional[str] = None


# ─── 会话模型 ────────────────────────────────────────────────────────────────

class SessionStatus(str, Enum):
    PENDING = "pending"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class TerminalSession(BaseModel):
    """一个交互式终端会话。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    host_id: Optional[str] = None
    title: str = "新终端"
    conn_type: ConnType = ConnType.LOCAL
    status: SessionStatus = SessionStatus.PENDING
    last_error: Optional[str] = None  # Shell 启动失败时由服务端写入，便于前端展示
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active: datetime = Field(default_factory=datetime.utcnow)
    # 连接参数（运行时填充）
    host: str = "127.0.0.1"
    port: int = 22
    username: str = ""
    password: Optional[str] = None
    # WinRM 专用（conn_type=winrm）
    winrm_port: int = 5985
    winrm_use_ssl: bool = False
    winrm_transport: str = "ntlm"
    winrm_server_cert_validation: str = "ignore"
    winrm_shell_mode: str = "interactive"
    ssh_private_key_path: Optional[str] = None
    ssh_private_key_passphrase: Optional[str] = None
    # 目标操作系统（供 NL→命令 / LLM 适配）；创建会话时由服务端根据连接方式与本机平台推断
    target_os: str = "linux"


# ─── 消息模型 ────────────────────────────────────────────────────────────────

class WsMessage(BaseModel):
    """WebSocket 消息格式。"""
    type: str                      # input | output | resize | status | error | llm_response
    session_id: str
    data: str = ""
    width: int = 80
    height: int = 24
    llm_response: Optional[str] = None


class ChatTurn(BaseModel):
    """Shell 历史中的自然语言对话轮次。"""
    role: str = "user"             # user | assistant | system
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─── LLM 增强 ───────────────────────────────────────────────────────────────

class PromptResult(BaseModel):
    """LLM 处理自然语言后的结果。"""
    should_execute: bool = True
    command: Optional[str] = None           # 要执行的 shell 命令
    explanation: str = ""                   # 给用户的解释
    warning: str = ""                       # 危险操作警告
    is_dangerous: bool = False              # 是否为危险操作
    confirm_required: bool = False          # 是否需要用户确认
    session_context: Optional[str] = None  # 当前会话上下文（最后几行输出）


class ClosureExecuteBody(BaseModel):
    """POST /api/hosts/{id}/closure-execute：在指定主机上跑闭环（独立 oneshot 连接，非当前 PTY）。"""

    command: str = Field(..., min_length=1, description="要执行的命令（可含换行，作为一段脚本由 oneshot 发送）")
    max_fix_attempts: int = Field(default=3, ge=0, le=10, description="失败时 LLM 修复轮数上限")
    nl_intent_hint: Optional[str] = Field(default=None, description="可选：原始自然语言意图，写入闭环包")
    success_mode: str = Field(
        default="exit_code",
        description="成败判定：exit_code | llm | both（both=退出码通过且 LLM 判定成功）",
    )
    archive_kb: bool = Field(
        default=False,
        description="为 true 时成功后将闭环包写入 JSONL 存档 + KnowledgeHub 知识库（供后续检索复用）",
    )
    mirror_session_id: Optional[str] = Field(
        default=None,
        description="若填写现有终端 session_id，则向该会话 WebSocket 推送 repair_* 时间线（右侧联动）。"
        "默认不把 oneshot 闭环输出写入左侧终端；若需旧式终端镜像请设环境变量 OPS_CLOSURE_MIRROR_TERMINAL=1。"
        "远端 POST .../hosts/.../closure-execute/stream 时 Web 终端应传当前 Tab 的 session_id；省略则仅 SSE、无右侧时间线联动，见 docs/closure-api.md",
    )
    shell_profile: Optional[str] = Field(
        default=None,
        description="本地闭环专用：unix | powershell；省略时按会话目标系统推断",
    )
    interactive_fix_preview: bool = Field(
        default=False,
        description="true 时（建议仅配合 .../closure-execute/stream）每轮 LLM 修复前先人机确认：采纳 / 改写 / 中止",
    )


class ClosureInteractiveResumeBody(BaseModel):
    """POST /api/closure-interactive/{trace_id}/resume — 唤醒人机共编闭环。"""

    action: str = Field(..., description="adopt | rewrite | abort")
    command: Optional[str] = Field(
        default=None,
        description="rewrite 时必填：替换本轮拟执行命令（单条）",
    )


class ClosureCausalNode(BaseModel):
    """因果链中的一段（用于前端箭头 / 状态色）。"""

    key: str = ""
    label: str = ""
    status: str = ""  # ok | fail | warn | skip | pending


class ClosureCognitiveLayer(BaseModel):
    """分层摘要：降低原始 stdout 的阅读负载（启发式生成）。"""

    what_happened: str = Field(default="", description="发生了什么（情境一句话）")
    outcome: str = Field(default="", description="本步结果（成败与依据）")
    next_suggestion: str = Field(default="", description="对用户下一步可读提示")


class ClosureStepResponse(BaseModel):
    phase: str
    command: str
    gateway_allowed: bool
    gateway_reason: str = ""
    pending_change_control: bool = Field(
        default=False,
        description="命中变更冻结窗口，命令已入待审批队列",
    )
    change_control_pending_id: str = Field(
        default="",
        description="待审批队列 id，可与 POST /api/pending-change-control/{id}/approve 联动",
    )
    gateway_detail: Optional[Dict[str, Any]] = Field(
        default=None,
        description="网关拒绝时的结构化标签：denial_category / rule_kind / matched_pattern / override_requires_approval 等",
    )
    exit_code: Optional[int] = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    stdout_full: str = Field(
        default="",
        description="该步标准输出全文（服务端单流最多约 512KB，超出见 stdout_truncated）",
    )
    stderr_full: str = Field(
        default="",
        description="该步标准错误全文",
    )
    stdout_truncated: bool = Field(default=False, description="true 表示原始 stdout 超过长度上限，仅返回前段")
    stderr_truncated: bool = Field(default=False, description="true 表示原始 stderr 超过长度上限")
    fix_round: int = 0
    step_title: str = Field(
        default="",
        description="展示用：首轮执行 / 自动修复 · 第 N 轮",
    )
    exit_ok: Optional[bool] = Field(
        default=None,
        description="是否满足配置的「成功退出码」判定（None：未执行或未判定）",
    )
    llm_judge_ok: Optional[bool] = Field(
        default=None,
        description="LLM 对该步输出是否判定为成功；exit_code 模式下为 null（未咨询 LLM）",
    )
    llm_judge_reason: str = Field(
        default="",
        description="LLM 判定附带的简短理由",
    )
    outcome_detail: str = Field(
        default="",
        description="综合成败摘要（如 both：exit=… llm=…）",
    )
    effective_command: str = Field(
        default="",
        description="闭环语义上的有效命令（通常即 ClosurePayload.effective_command；与传输无关的展示主轴）",
    )
    transport: str = Field(
        default="",
        description="传输类型：ssh | winrm | local（与 ExecResult / ClosurePayload 对齐）",
    )
    risk_level: str = Field(
        default="",
        description="启发式风险分层：low | medium | high | critical",
    )
    cognitive: Optional[ClosureCognitiveLayer] = Field(
        default=None,
        description="分层摘要卡片（与原始 stdout_full 并存）",
    )
    causal_chain: List[ClosureCausalNode] = Field(
        default_factory=list,
        description="触发→网关→执行→判定 结构化节点",
    )
    causal_chain_text: str = Field(
        default="",
        description="单行因果链文本，便于复制与终端摘要",
    )


class ClosureExecuteResponse(BaseModel):
    ok: bool
    stop_reason: str
    steps: List[ClosureStepResponse] = Field(default_factory=list)
    final_exit_code: Optional[int] = None
    trace_id: str = Field(
        default="",
        description="本次闭环追踪 id，与日志/审计一致",
    )
    stop_reason_detail: str = Field(
        default="",
        description="停止原因可读说明（与 repair SSE / 前端对齐）",
    )
    success_mode: str = Field(
        default="exit_code",
        description="请求使用的成败模式：exit_code | llm | both",
    )
    replay_bundle_saved: bool = Field(
        default=False,
        description="是否已写入可审计 Replay Bundle（data/replay_bundles/{trace_id}.json）",
    )
    replay_bundle_href: str = Field(
        default="",
        description="获取回放包的 GET 路径，如 /api/replay-bundles/cl_…",
    )
    kb_pending_candidate_id: Optional[str] = Field(
        default=None,
        description="闭环成功后生成的 KB 候选 id（待人工批准入库），见 /api/kb/pending",
    )
    kb_pending_href: str = Field(
        default="",
        description="GET 候选详情路径，如 /api/kb/pending/{id}",
    )


class IntentBroadcastPreviewBody(BaseModel):
    """POST /api/intent-broadcast/preview — NL 意图广播预检。"""

    nl_intent: str = Field(..., min_length=1, description="自然语言运维意图")
    tag: Optional[str] = Field(
        default=None,
        description="主机标签（与 Host.tags 匹配），如 web-servers",
    )
    host_ids: Optional[List[str]] = Field(
        default=None,
        description="显式指定主机 id 列表；与 tag 二选一或合并（合并时取并集）",
    )


class IntentBroadcastDispatchBody(IntentBroadcastPreviewBody):
    """POST /api/intent-broadcast/dispatch — 分段翻译后并行下发。"""

    parallel: bool = Field(default=True, description="是否并行执行各主机")
    ignore_warnings: bool = Field(
        default=False,
        description="为 true 时在存在 warning 级冲突时仍执行（error 仍阻止）",
    )
    max_concurrency: int = Field(default=8, ge=1, le=32)


class LLMModelEntry(BaseModel):
    """``data/llm_models.json`` 中单条模型（OpenAI 兼容 API）。"""

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())

    model_name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    api_key: str = ""
    max_tokens: int = Field(default=4096, ge=256, le=128000)
    allow_thinking: bool = False
    inference_model: Optional[str] = Field(
        default=None,
        description="请求体中的 model 字段；省略则与 model_name 相同",
    )


class LLMConfigUpdate(BaseModel):
    """PUT /api/llm/config：多模型（``models``）或旧版 ``llm_config.json``。"""

    model_config = ConfigDict(populate_by_name=True)

    schema_version: Optional[int] = None
    selected_model_name: Optional[str] = None
    models: Optional[List[LLMModelEntry]] = None

    mode: Optional[str] = None
    display_name: str = ""
    base_url: str = ""
    api_key: Optional[str] = None
    llm_model: str = Field(default="", alias="model")
    builtin_provider: Optional[str] = None
    no_think: Optional[bool] = None
    http_timeout_sec: Optional[float] = None
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="0～2"
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=256, le=128000, description="单次回复上限 token"
    )
