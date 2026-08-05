"""错误迭代修正流程 — 数据模型（结构化解析与修正方案）。"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ErrorCategory(str, Enum):
    """错误分类（解析器产出；含细分类型以保持兼容：旧值仍可用）。"""

    PERMISSION_DENIED = "permission_denied"  # 权限不足（通用）
    PERMISSION_DENIED_SUDO = "permission_denied_sudo"  # 需 sudo / 系统路径写保护
    FILE_NOT_FOUND = "file_not_found"  # 文件/目录不存在（通用）
    FILE_NOT_FOUND_PATH_TYPO = "file_not_found_path_typo"  # 疑似路径拼写错误（如 Did you mean）
    PATH_ERROR = "path_error"  # 路径错误（含无效路径）
    NETWORK = "network"  # 网络相关（通用）
    NETWORK_TIMEOUT_UNREACHABLE = "network_timeout_unreachable"  # 超时/不可达/无路由
    SYNTAX = "syntax"  # 语法/脚本错误
    COMMAND_NOT_FOUND = "command_not_found"  # 命令未找到 127 等（通用）
    COMMAND_NOT_FOUND_PKG_MISSING = "command_not_found_pkg_missing"  # 包未安装（如 mvn/node）
    DEPENDENCY_MISSING = "dependency_missing"  # 依赖未安装（历史兼容，偏上层语义）
    SERVER_UNAVAILABLE = "server_unavailable"  # 服务不可用/宕机
    HARDWARE = "hardware"  # 硬件故障提示
    UNKNOWN = "unknown"


class Fixability(str, Enum):
    """可修正性判定（第二步）。"""

    FIXABLE = "fixable"
    NOT_FIXABLE = "not_fixable"
    NEEDS_HUMAN = "needs_human"


class EnvironmentSnapshot(BaseModel):
    """执行环境信息（传入 LLM / 知识库）。"""

    os_name: str = Field(default="", description="如 Linux / Darwin / Windows")
    os_version: str = Field(default="", description="发行版或版本摘要")
    shell: str = Field(default="", description="当前 shell")
    current_user: str = Field(default="", description="当前用户")
    is_root_or_sudo: bool = Field(default=False, description="是否 root 或具备 sudo")
    cwd: str = Field(default="", description="工作目录")
    extra: Dict[str, Any] = Field(default_factory=dict)


class StructuredError(BaseModel):
    """第一步：结构化解析结果。"""

    error_category: ErrorCategory = Field(description="错误类型")
    return_code: int = Field(description="进程退出码")
    path: Optional[str] = Field(default=None, description="涉及路径（若有）")
    reason: str = Field(default="", description="人类可读原因说明")
    stderr_snippet: str = Field(default="", description="stderr 摘要")
    stdout_snippet: str = Field(default="", description="stdout 摘要（辅助）")
    raw_stderr: str = Field(default="", description="完整 stderr")
    raw_stdout: str = Field(default="", description="完整 stdout")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="附加键值，如 matched_pattern")
    requires_package: Optional[str] = Field(
        default=None,
        description="若能推断缺失的软件包名（如 nginx、nodejs），供 LLM/人工安装参考",
    )

    @property
    def display_type_cn(self) -> str:
        _map = {
            ErrorCategory.PERMISSION_DENIED: "权限不足",
            ErrorCategory.PERMISSION_DENIED_SUDO: "权限不足（需 sudo）",
            ErrorCategory.FILE_NOT_FOUND: "文件不存在",
            ErrorCategory.FILE_NOT_FOUND_PATH_TYPO: "文件不存在（疑似路径拼写）",
            ErrorCategory.PATH_ERROR: "路径错误",
            ErrorCategory.NETWORK: "网络错误",
            ErrorCategory.NETWORK_TIMEOUT_UNREACHABLE: "网络超时/不可达",
            ErrorCategory.SYNTAX: "语法错误",
            ErrorCategory.COMMAND_NOT_FOUND: "命令未找到",
            ErrorCategory.COMMAND_NOT_FOUND_PKG_MISSING: "命令未找到（包未安装）",
            ErrorCategory.DEPENDENCY_MISSING: "依赖缺失",
            ErrorCategory.SERVER_UNAVAILABLE: "服务不可用",
            ErrorCategory.HARDWARE: "硬件故障",
            ErrorCategory.UNKNOWN: "未知",
        }
        return _map.get(self.error_category, str(self.error_category))


class HistorySegment(BaseModel):
    """修正历史链中的单段：原始命令 / 错误 / 修正命令。"""

    kind: Literal["original_command", "error", "fix_command"]
    text: str = Field(min_length=1)


class RemediationHistory(BaseModel):
    """修正历史链（第二步要求）：原始命令 -> 错误1 -> 修正1 -> 错误2 -> …"""

    segments: List[HistorySegment] = Field(default_factory=list)

    def append(self, kind: Literal["original_command", "error", "fix_command"], text: str) -> None:
        self.segments.append(HistorySegment(kind=kind, text=text.strip()))

    def to_prompt_string(self) -> str:
        parts: List[str] = []
        for s in self.segments:
            if s.kind == "original_command":
                parts.append(f"原始命令: {s.text}")
            elif s.kind == "error":
                parts.append(f"错误: {s.text}")
            else:
                parts.append(f"修正命令: {s.text}")
        return " -> ".join(parts)

    def format_arrow_chain(self) -> str:
        """需求文档中的箭头链格式。"""
        labels = []
        for s in self.segments:
            if s.kind == "original_command":
                labels.append(s.text)
            elif s.kind == "error":
                labels.append(f"错误:{s.text[:200]}")
            else:
                labels.append(s.text)
        return " -> ".join(labels)


class LLMRemediationJSON(BaseModel):
    """第三步：大模型必须输出的 JSON 结构。"""

    root_cause: str = Field(description="根因说明（自然语言）")
    fixed_command: str = Field(description="修正后的具体命令（单行或可执行脚本块）")
    risk_warning: str = Field(default="", description="风险提示")
    requires_precheck_script: bool = Field(
        default=False,
        description="是否建议带前置检查的脚本（环境动态变化）",
    )
    notes: str = Field(default="", description="可选补充说明")
    confidence_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="修复置信度 0~1（可由包装层根据 KB/Lite/LLM 与风险重算）",
    )


class KnowledgeRecord(BaseModel):
    """第五步：经验沉淀记录。"""

    error_category: ErrorCategory
    env_os: str = ""
    env_privilege: str = ""  # 如 user:root / user:alice
    original_command: str
    fixed_command: str
    root_cause: str = ""
    stderr_snippet: str = ""
    fingerprint: str = ""
    requires_package: Optional[str] = Field(
        default=None,
        description="缺失包名（与 StructuredError.requires_package 对齐，供检索 Tier2）",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("created_at", mode="before")
    @classmethod
    def ensure_dt(cls, v: Any) -> Any:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        return v


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# /tmp/foo、/var/tmp/foo
_TMP_PATH = re.compile(r"(?:/tmp/|/var/tmp/)[^\s]+")
_HOME_UNIX = re.compile(r"/home/[^/\s]+(?:/[^\s]+)?")
_WIN_USER = re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+(?:\\[^\s]+)?", re.IGNORECASE)
# 常见 PID / 短数字串（避免误伤版本号，限制长度）
_PID_LIKE = re.compile(r"\bpid\s*[=:]?\s*\d{2,7}\b", re.IGNORECASE)
_STANDALONE_LONG_DIGITS = re.compile(r"\b\d{10,14}\b")
_ISO_DATETIME = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
)
_HEX_TOKEN = re.compile(r"\b[a-f0-9]{8,}\b", re.IGNORECASE)
_RANDOM_SUFFIX = re.compile(r"[_./\\-][a-f0-9]{6,12}\b", re.IGNORECASE)


def normalize_command_for_fingerprint(cmd: str) -> str:
    """
    弱化路径噪声后再参与指纹：去掉 /tmp、/home、时间戳、PID、随机 hex 等。
    保留命令名与参数骨架（如 cp <TMP> <DST>）。
    """
    if not cmd:
        return ""
    s = cmd.strip()
    s = _ANSI_ESCAPE.sub("", s)
    s = _TMP_PATH.sub("<TMP>", s)
    s = _HOME_UNIX.sub("<HOME>", s)
    s = _WIN_USER.sub("<HOME>", s)
    s = _ISO_DATETIME.sub("<DT>", s)
    s = _STANDALONE_LONG_DIGITS.sub("<TS>", s)
    s = _PID_LIKE.sub("<PID>", s)
    s = _HEX_TOKEN.sub("<HEX>", s)
    s = _RANDOM_SUFFIX.sub("<RND>", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_text_for_fingerprint(text: str) -> str:
    """stderr 等长文本的规范化（与命令同源规则，便于 Tier3 模糊匹配）。"""
    return normalize_command_for_fingerprint(text)


def os_fingerprint_key(env: EnvironmentSnapshot) -> str:
    """参与指纹的环境片段（与存储侧写入时一致）。"""
    parts = [
        (env.os_name or "").strip().lower(),
        (env.os_version or "").strip().lower(),
    ]
    return "|".join(parts)


def compute_error_fingerprint(error_type: str, normalized_cmd: str, os_info: str) -> str:
    """sha256(error_type:normalized_cmd:os_info) 十六进制小写。"""
    raw = f"{error_type}:{normalized_cmd}:{os_info}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class HumanIntervention(BaseModel):
    """不可修正时的输出。"""

    reason: str
    suggestions: List[str] = Field(default_factory=list)
    fixability: Fixability = Fixability.NEEDS_HUMAN


class CommandExecutionOutcome(BaseModel):
    """一次命令执行结果（包装器入参）。"""

    command: str
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    confidence_score: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="与该次执行相关的置信度（若有）",
    )


class RemediationTerminationReason(str, Enum):
    # SIMILAR_FIX_LOOP: 与上一轮修正命令几乎完全相同；LOOP_DETECTED_SEMANTIC: 双重相似度判定语义雷同
    SUCCESS = "success"
    MAX_RETRIES = "max_retries"
    SIMILAR_FIX_LOOP = "similar_fix_loop"
    LOOP_DETECTED_SEMANTIC = "loop_detected_semantic"
    USER_ABORT = "user_abort"
    NOT_FIXABLE = "not_fixable"
    MANUAL_ADJUST = "manual_adjust"
    LLM_FAILURE = "llm_failure"
    LOW_CONFIDENCE = "low_confidence"


class RemediationSessionResult(BaseModel):
    """主循环结束时的汇总。"""

    termination: RemediationTerminationReason
    message: str = ""
    final_command: Optional[str] = None
    history: RemediationHistory = Field(default_factory=RemediationHistory)
    knowledge_saved: bool = False
    confidence_score: Optional[float] = Field(
        default=None,
        description="末次提案对应的置信度（成功或 LOW_CONFIDENCE 等场景）",
    )
