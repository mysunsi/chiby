"""KnowledgeHub — 数据模型定义。"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# 知识库条目
# ─────────────────────────────────────────────────────────────────────────────

class KBCategory(str, Enum):
    """知识类别"""
    SYSTEM_MONITOR = "system_monitor"       # 系统监控
    USER_MANAGEMENT = "user_management"     # 用户管理
    PACKAGE_MANAGEMENT = "package_management"  # 包管理
    SERVICE_OPS = "service_ops"             # 服务运维
    NETWORK_OPS = "network_ops"             # 网络运维
    SECURITY = "security"                   # 安全相关
    DATABASE = "database"                   # 数据库
    DOCKER_K8S = "docker_k8s"               # 容器/K8s
    FAILURE_RECOVERY = "failure_recovery"   # 故障恢复
    OTHER = "other"                         # 其他


class KBConfidence(str, Enum):
    HIGH = "high"       # 多次验证可靠
    MEDIUM = "medium"   # 单次验证
    LOW = "low"         # 经验推断


class KBEntry(BaseModel):
    """单条知识库记录（故障经验）"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = Field(..., description="简洁标题，如：nginx 连接数过高导致 502")
    category: KBCategory = KBCategory.OTHER
    # 症状描述（用于检索匹配）
    symptom: str = Field(..., description="故障表现：错误信息/日志/现象")
    # 根因分析
    root_cause: str = Field(..., description="根本原因分析")
    # 修复方案（命令或步骤列表）
    remediation: str = Field(..., description="修复命令或步骤（可执行）")
    # 验证方法
    verify_method: Optional[str] = Field(None, description="如何验证修复成功")
    # 适用环境
    applicable_os: List[str] = Field(
        default_factory=list,
        description="适用操作系统，如 ['linux', 'windows', 'ubuntu', 'centos']"
    )
    applicable_service: Optional[str] = Field(None, description="适用服务，如 'nginx', 'mysql', 'docker'")
    tags: List[str] = Field(default_factory=list, description="标签，便于分类检索")
    # 关联的原始错误指纹
    error_fingerprint: Optional[str] = Field(None, description="关联的原始错误指纹")
    # 关联的命令
    original_command: Optional[str] = Field(None, description="触发该知识的原始命令")
    # 置信度
    confidence: KBConfidence = KBConfidence.MEDIUM
    # 来源
    source: str = Field(..., description="知识来源，如 'remediator', 'manual', 'terminal_session'")
    source_id: Optional[str] = Field(None, description="来源记录ID，如 trace_id / task_id")
    # 成功次数
    success_count: int = Field(default=0, description="成功应用次数")
    failure_count: int = Field(default=0, description="应用失败次数")
    # 评分（0-5分，用户反馈）
    rating: float = Field(default=0.0, ge=0, le=5, description="用户评分")
    rating_count: int = Field(default=0, description="评分次数")
    # 备注
    notes: Optional[str] = Field(None, description="补充说明或注意事项")
    # 元数据
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(default="system", description="创建者")

    def update_rating(self, new_rating: float) -> None:
        """增量更新评分（算法：加权平均）"""
        total = self.rating * self.rating_count + new_rating
        self.rating_count += 1
        self.rating = round(total / self.rating_count, 2)
        self.updated_at = datetime.utcnow()

    def record_success(self) -> None:
        self.success_count += 1
        if self.success_count >= 3 and self.confidence == KBConfidence.MEDIUM:
            self.confidence = KBConfidence.HIGH
        self.updated_at = datetime.utcnow()

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.failure_count >= 2 and self.confidence == KBConfidence.HIGH:
            self.confidence = KBConfidence.MEDIUM
        self.updated_at = datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# 脚本库条目
# ─────────────────────────────────────────────────────────────────────────────

class ScriptLanguage(str, Enum):
    BASH = "bash"
    POWERSHELL = "powershell"
    PYTHON = "python"
    SQL = "sql"
    YAML = "yaml"
    OTHER = "other"


class ScriptRiskLevel(str, Enum):
    SAFE = "safe"           # 只读查询，无破坏性
    MEDIUM = "medium"       # 修改配置，需确认
    HIGH = "high"           # 修改数据/删除资源
    CRITICAL = "critical"   # 不可逆操作，强制二次确认


class ScriptEntry(BaseModel):
    """单条脚本记录"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    name: str = Field(..., description="脚本名称，如：nginx 连接数过高处理")
    description: str = Field(..., description="脚本功能描述")
    # 脚本内容（核心）
    content: str = Field(..., description="脚本内容")
    language: ScriptLanguage = ScriptLanguage.BASH
    # 适用环境
    applicable_os: List[str] = Field(default_factory=list)
    # 参数定义（JSON Schema 格式）
    parameters: Optional[Dict[str, Any]] = Field(
        None,
        description="参数定义，JSON Schema 格式，示例：{'type':'object','properties':{'port':{'type':'integer','description':'端口号'}}}"
    )
    # 参数示例
    parameter_examples: Optional[Dict[str, Any]] = Field(
        None,
        description="参数示例值，示例：{'port': 8080, 'host': 'localhost'}"
    )
    # 执行前置条件
    prerequisites: Optional[str] = Field(None, description="执行前置条件")
    # 风险等级
    risk_level: ScriptRiskLevel = ScriptRiskLevel.MEDIUM
    # 预期执行时长（秒）
    expected_duration_sec: int = Field(default=30, ge=0, le=3600)
    # 分类标签
    category: KBCategory = KBCategory.OTHER
    tags: List[str] = Field(default_factory=list)
    # 版本管理
    version: str = Field(default="1.0.0", description="语义化版本")
    version_notes: Optional[str] = Field(None, description="版本更新说明")
    # 关联知识库条目
    related_kb_ids: List[str] = Field(default_factory=list, description="关联的 KBEntry id 列表")
    # 统计
    use_count: int = Field(default=0, description="使用次数")
    success_count: int = Field(default=0)
    failure_count: int = Field(default=0)
    # 元数据
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str = Field(default="system")

    def record_use(self, success: bool) -> None:
        self.use_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.updated_at = datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# 最佳实践
# ─────────────────────────────────────────────────────────────────────────────

class BestPractice(BaseModel):
    """最佳实践条目"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    title: str = Field(..., description="最佳实践标题")
    description: str = Field(..., description="简要说明")
    # 详细步骤（Markdown 格式）
    steps: str = Field(..., description="详细步骤，Markdown 格式")
    # 适用场景
    applicable_scenarios: List[str] = Field(default_factory=list)
    applicable_os: List[str] = Field(default_factory=list)
    category: KBCategory = KBCategory.OTHER
    tags: List[str] = Field(default_factory=list)
    # 来源文档
    source_url: Optional[str] = Field(None)
    # 元数据
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────────────────────────────────────
# 搜索相关
# ─────────────────────────────────────────────────────────────────────────────

class IngestSource(str, Enum):
    REMEDIATOR_SUCCESS = "remediator_success"    # remediator 成功案例
    TERMINAL_SESSION = "terminal_session"        # terminal 成功会话
    MANUAL = "manual"                              # 手动录入
    IMPORT = "import"                             # 批量导入
    CLOSURE_APPROVED = "closure_approved"         # 闭环候选经人工批准入库
    AI_DIAGNOSIS = "ai_diagnosis"                 # 掌上多机/单机 AI 排查入库
    FLEET_REPORT = "fleet_report"                 # Fleet 巡检报告沉淀


class SearchQuery(BaseModel):
    """搜索查询参数"""
    q: str = Field(..., description="查询文本（自然语言）")
    mode: Literal["kb", "script", "best_practice", "all"] = "all"
    category: Optional[KBCategory] = None
    tags: List[str] = Field(default_factory=list)
    applicable_os: Optional[str] = None
    min_confidence: Optional[KBConfidence] = None
    min_rating: Optional[float] = Field(None, ge=0, le=5)
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


class SearchResult(BaseModel):
    """单条搜索结果"""
    entry_type: Literal["kb", "script", "best_practice"] = Field(...)
    entry_id: str = Field(...)
    title: str = Field(...)
    snippet: str = Field(..., description="匹配片段（高亮关键词）")
    score: float = Field(..., description="相关度分数 0-1")
    category: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    language: Optional[str] = None
    risk_level: Optional[str] = None
    confidence: Optional[str] = None
    applicable_os: List[str] = Field(default_factory=list)
    use_count: Optional[int] = None
    success_rate: Optional[float] = Field(None, description="成功率 0-1")


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str
    total: int
    results: List[SearchResult]
    mode: str
    took_ms: int = Field(..., description="检索耗时（毫秒）")


# ─────────────────────────────────────────────────────────────────────────────
# 闭环候选入库（人工批准后写入 KB）
# ─────────────────────────────────────────────────────────────────────────────


class PendingKBStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class KBPendingCandidate(BaseModel):
    """闭环成功后生成的 KB 候选条目（待人工确认）。"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    trace_id: str = ""
    status: PendingKBStatus = PendingKBStatus.PENDING
    title: str = ""
    tags: List[str] = Field(default_factory=list)
    host_profile: Dict[str, Any] = Field(default_factory=dict, description="主机画像（无密钥）")
    command_chain_redacted: str = ""
    output_summary: str = ""
    symptom: str = ""
    root_cause: str = ""
    remediation: str = ""
    suggested_category: str = "failure_recovery"
    nl_intent_hint: Optional[str] = None
    closure_stop_reason: str = ""
    step_count: int = 0
    shell_profile: str = "unix"
    raw_steps_digest: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_at: Optional[datetime] = None
    reviewed_by: str = ""
    reject_reason: Optional[str] = None
