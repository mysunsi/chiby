"""KnowledgeHub — 知识自动沉淀。

从以下来源自动沉淀知识：
1. remediator 成功案例 → KBEntry
2. terminal 成功会话 → KBEntry / ScriptEntry
3. 手动录入 → KBEntry / ScriptEntry / BestPractice

调用方式：
  from chibycore.knowledge_hub.ingestion import KnowledgeIngester
  ingester = KnowledgeIngester()
  ingester.ingest_from_remediator(trace_id, error_info, remediation_steps, success=True)
  ingester.ingest_from_terminal(command, stdout, stderr, success=True)
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from chibycore.knowledge_hub.models import (
    BestPractice,
    IngestSource,
    KBCategory,
    KBConfidence,
    KBEntry,
    ScriptEntry,
    ScriptLanguage,
    ScriptRiskLevel,
)
from chibycore.knowledge_hub.storage import KnowledgeHubStorage

logger = logging.getLogger(__name__)


class KnowledgeIngester:
    """
    知识沉淀入口。自动将成功经验入库，重复经验自动去重。
    """

    def __init__(self, storage: Optional[KnowledgeHubStorage] = None) -> None:
        self._storage = storage or KnowledgeHubStorage.get_instance()
        self._dedup_cache: Dict[str, str] = {}  # fingerprint → entry_id

    # ── 自动化沉淀 ─────────────────────────────────────────────────────────────

    def ingest_from_remediator(
        self,
        trace_id: str,
        error_info: Dict[str, Any],
        remediation_steps: List[Dict[str, Any]],
        success: bool = True,
        environment_id: Optional[str] = None,
    ) -> Optional[KBEntry]:
        """
        从 remediator 成功案例沉淀为 KBEntry。

        Args:
            trace_id: 追溯 ID
            error_info: 包含 error_fingerprint / error_type / error_message 等
            remediation_steps: 修复步骤列表
            success: 是否成功
        """
        if not success:
            return None

        fingerprint = error_info.get("error_fingerprint", "")
        if not fingerprint:
            fingerprint = self._make_fingerprint(
                error_info.get("error_message", ""), remediation_steps[0].get("command", "") if remediation_steps else ""
            )

        # 去重
        if fingerprint in self._dedup_cache:
            entry_id = self._dedup_cache[fingerprint]
            entry = self._storage.get_kb_entry(entry_id)
            if entry:
                entry.record_success()
                self._storage.save_kb_entry(entry)
                return entry

        # 提取信息
        error_msg = error_info.get("error_message", "")
        error_type = error_info.get("error_type", "")
        symptom = self._summarize_symptom(error_msg, error_type)
        root_cause = self._infer_root_cause(error_msg, error_type)
        remediation = self._steps_to_text(remediation_steps)
        category = self._infer_category(remediation_steps, error_msg)

        entry = KBEntry(
            id=str(uuid.uuid4())[:12],
            title=f"{error_type or '故障'}: {symptom[:40]}",
            category=category,
            symptom=symptom,
            root_cause=root_cause,
            remediation=remediation,
            verify_method=self._extract_verify_method(remediation_steps),
            applicable_os=self._extract_os_from_steps(remediation_steps),
            error_fingerprint=fingerprint,
            original_command=remediation_steps[0].get("command", "") if remediation_steps else "",
            confidence=KBConfidence.MEDIUM,
            source=IngestSource.REMEDIATOR_SUCCESS.value,
            source_id=trace_id,
            success_count=1,
        )

        self._storage.save_kb_entry(entry)
        self._dedup_cache[fingerprint] = entry.id
        logger.info(f"[KnowledgeHub] 沉淀 KBEntry: {entry.id} - {entry.title}")
        return entry

    def ingest_from_terminal_session(
        self,
        command: str,
        nl_intent: str,
        stdout: str,
        stderr: str,
        exit_code: int,
        os_hint: Optional[str] = None,
        created_by: str = "terminal",
    ) -> Optional[KBEntry]:
        """
        从 terminal 成功会话沉淀为 KBEntry（简单命令场景）。
        仅当 exit_code == 0 且 stderr 无错误信息时沉淀。
        """
        if exit_code != 0:
            return None

        # 过滤掉纯查询类命令（查看信息不需要沉淀为故障经验）
        if self._is_readonly_command(command):
            return None

        fingerprint = self._make_fingerprint(f"{command}|{stderr}", "")
        if fingerprint in self._dedup_cache:
            return None

        symptom = f"用户输入: {nl_intent} | 执行命令: {command}"
        entry = KBEntry(
            id=str(uuid.uuid4())[:12],
            title=f"操作经验: {nl_intent[:50]}",
            category=self._infer_category_from_command(command),
            symptom=symptom,
            root_cause="用户主动操作",
            remediation=command,
            verify_method=f"检查 exit_code == 0，输出: {stdout[:200]}",
            applicable_os=[os_hint] if os_hint else [],
            original_command=command,
            confidence=KBConfidence.LOW,
            source=IngestSource.TERMINAL_SESSION.value,
            success_count=1,
            created_by=created_by,
        )

        self._storage.save_kb_entry(entry)
        self._dedup_cache[fingerprint] = entry.id
        logger.info(f"[KnowledgeHub] 从终端会话沉淀: {entry.id}")
        return entry

    # ── 脚本注册 ──────────────────────────────────────────────────────────────

    def register_script(
        self,
        name: str,
        description: str,
        content: str,
        language: ScriptLanguage = ScriptLanguage.BASH,
        risk_level: ScriptRiskLevel = ScriptRiskLevel.MEDIUM,
        category: KBCategory = KBCategory.OTHER,
        tags: Optional[List[str]] = None,
        parameters: Optional[Dict[str, Any]] = None,
        parameter_examples: Optional[Dict[str, Any]] = None,
        prerequisites: Optional[str] = None,
        applicable_os: Optional[List[str]] = None,
        created_by: str = "manual",
    ) -> ScriptEntry:
        """手动注册脚本到脚本库。"""
        entry = ScriptEntry(
            id=str(uuid.uuid4())[:12],
            name=name,
            description=description,
            content=content,
            language=language,
            risk_level=risk_level,
            category=category,
            tags=tags or [],
            parameters=parameters,
            parameter_examples=parameter_examples,
            prerequisites=prerequisites,
            applicable_os=applicable_os or [],
            created_by=created_by,
        )
        self._storage.save_script_entry(entry)
        logger.info(f"[KnowledgeHub] 注册脚本: {entry.id} - {entry.name}")
        return entry

    def register_from_generated_script(
        self,
        nl_intent: str,
        script_content: str,
        language: ScriptLanguage,
        executed: bool = False,
        execution_success: bool = False,
        created_by: str = "agent",
    ) -> ScriptEntry:
        """
        将 Agent 生成的脚本自动注册到脚本库（当用户标记"保存到脚本库"时）。
        """
        entry = ScriptEntry(
            id=str(uuid.uuid4())[:12],
            name=nl_intent[:60] if len(nl_intent) > 60 else nl_intent,
            description=f"AI 生成脚本：{nl_intent}",
            content=script_content,
            language=language,
            risk_level=self._assess_script_risk(script_content),
            category=self._infer_category_from_command(script_content),
            tags=["ai-generated", "agent"] if not executed else ["ai-generated", "verified"],
            applicable_os=["linux"] if language in (ScriptLanguage.BASH, ScriptLanguage.PYTHON) else ["windows"],
            use_count=1 if executed else 0,
            success_count=1 if execution_success else 0,
            created_by=created_by,
        )
        self._storage.save_script_entry(entry)
        return entry

    # ── 最佳实践录入 ─────────────────────────────────────────────────────────

    def register_best_practice(
        self,
        title: str,
        description: str,
        steps: str,
        applicable_scenarios: Optional[List[str]] = None,
        applicable_os: Optional[List[str]] = None,
        category: KBCategory = KBCategory.OTHER,
        tags: Optional[List[str]] = None,
        source_url: Optional[str] = None,
    ) -> BestPractice:
        """录入最佳实践。"""
        entry = BestPractice(
            id=str(uuid.uuid4())[:12],
            title=title,
            description=description,
            steps=steps,
            applicable_scenarios=applicable_scenarios or [],
            applicable_os=applicable_os or [],
            category=category,
            tags=tags or [],
            source_url=source_url,
        )
        self._storage.save_best_practice(entry)
        logger.info(f"[KnowledgeHub] 录入最佳实践: {entry.id} - {entry.title}")
        return entry

    # ── 辅助方法 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _make_fingerprint(error_msg: str, command: str) -> str:
        """生成错误指纹（简化版：取关键词哈希）。"""
        key_text = f"{error_msg}|{command}"
        key_text = re.sub(r"\d{4,}", "NUM", key_text)  # 数字替换
        key_text = re.sub(r"[a-f0-9]{8,}", "HEX", key_text)  # 长十六进制替换
        import hashlib
        return hashlib.md5(key_text.encode()).hexdigest()[:16]

    @staticmethod
    def _summarize_symptom(error_msg: str, error_type: str) -> str:
        """从错误信息提取症状描述。"""
        if error_msg:
            return error_msg[:200].strip()
        if error_type:
            return error_type
        return "未知故障"

    @staticmethod
    def _infer_root_cause(error_msg: str, error_type: str) -> str:
        """根据错误类型推断根因（简单规则，后续可升级为 LLM 分析）。"""
        low = (error_msg + error_type).lower()
        if "permission denied" in low or "access denied" in low:
            return "权限不足"
        if "not found" in low or "不存在" in low:
            return "资源不存在或路径错误"
        if "connection refused" in low or "连接被拒绝" in low:
            return "服务未启动或端口未监听"
        if "timeout" in low or "超时" in low:
            return "网络超时或服务响应慢"
        if "out of memory" in low or "内存不足" in low:
            return "内存资源耗尽"
        if "disk full" in low or "磁盘满" in low:
            return "磁盘空间不足"
        if "already exists" in low or "已存在" in low:
            return "资源重复创建"
        return "待分析（建议通过 LLM 进一步诊断）"

    @staticmethod
    def _steps_to_text(steps: List[Dict[str, Any]]) -> str:
        """将步骤列表转为可读文本。"""
        if not steps:
            return ""
        lines = []
        for i, step in enumerate(steps, 1):
            cmd = step.get("command", "")
            desc = step.get("description", "")
            if desc:
                lines.append(f"{i}. {desc}: `{cmd}`")
            else:
                lines.append(f"{i}. `{cmd}`")
        return "\n".join(lines)

    @staticmethod
    def _extract_verify_method(steps: List[Dict[str, Any]]) -> Optional[str]:
        """从步骤中提取验证方法。"""
        for step in steps:
            if "verify" in str(step.get("description", "")).lower():
                return step.get("command", "")
        return None

    @staticmethod
    def _extract_os_from_steps(steps: List[Dict[str, Any]]) -> List[str]:
        """从步骤命令推断适用 OS。"""
        os_list = []
        for step in steps:
            cmd = step.get("command", "")
            if any(x in cmd for x in ["df -h", "free -h", "systemctl", "apt ", "yum ", "docker "]):
                if "linux" not in os_list:
                    os_list.append("linux")
            if any(x in cmd for x in ["Get-", "Test-NetConnection", "choco"]):
                if "windows" not in os_list:
                    os_list.append("windows")
        return os_list or ["linux"]

    @staticmethod
    def _infer_category(steps: List[Dict[str, Any]], error_msg: str) -> KBCategory:
        """推断分类。"""
        all_text = " ".join([s.get("command", "") for s in steps]) + " " + error_msg
        low = all_text.lower()
        if any(x in low for x in ["nginx", "apache", "mysql", "redis", "service"]):
            return KBCategory.SERVICE_OPS
        if any(x in low for x in ["useradd", "passwd", "chmod", "chown"]):
            return KBCategory.USER_MANAGEMENT
        if any(x in low for x in ["docker", "kubectl", "k8s", "pod", "container"]):
            return KBCategory.DOCKER_K8S
        if any(x in low for x in ["cpu", "mem", "disk", "load", "top"]):
            return KBCategory.SYSTEM_MONITOR
        if any(x in low for x in ["iptables", "firewall", "netstat", "ss "]):
            return KBCategory.NETWORK_OPS
        if any(x in low for x in ["apt", "yum", "dnf", "pip", "npm"]):
            return KBCategory.PACKAGE_MANAGEMENT
        return KBCategory.FAILURE_RECOVERY

    @staticmethod
    def _infer_category_from_command(command: str) -> KBCategory:
        """从命令推断分类。"""
        low = command.lower()
        if any(x in low for x in ["useradd", "passwd", "usermod"]):
            return KBCategory.USER_MANAGEMENT
        if any(x in low for x in ["systemctl", "service ", "nginx", "apache"]):
            return KBCategory.SERVICE_OPS
        if any(x in low for x in ["df -h", "free -h", "top", "ps aux"]):
            return KBCategory.SYSTEM_MONITOR
        if any(x in low for x in ["docker", "kubectl"]):
            return KBCategory.DOCKER_K8S
        if any(x in low for x in ["iptables", "firewall-cmd", "netstat"]):
            return KBCategory.NETWORK_OPS
        if any(x in low for x in ["apt", "yum", "dnf", "pip", "choco"]):
            return KBCategory.PACKAGE_MANAGEMENT
        return KBCategory.OTHER

    @staticmethod
    def _is_readonly_command(command: str) -> bool:
        """判断是否为只读查询命令（这类命令不沉淀为故障经验）。"""
        low = command.lower().strip()
        readonly_patterns = [
            "ls ", "cat ", "pwd", "whoami", "uptime", "df -h", "free -h",
            "top", "ps aux", "ps -ef", "netstat", "ss -t", "hostname",
            "uname", "id ", "w ", "last", "df -i", "mount", "cat /proc/",
            "Get-Process", "Get-Service", "Get-NetTCPConnection", "Test-NetConnection",
            "dir ", "type ", "ver", "hostname",
        ]
        return any(low.startswith(p.strip()) for p in readonly_patterns)

    @staticmethod
    def _assess_script_risk(script_content: str) -> ScriptRiskLevel:
        """评估脚本风险等级。"""
        low = script_content.lower()
        dangerous = ["rm -rf", "dd ", "mkfs", "drop ", "truncate", "shutdown", "init 0", "reboot"]
        if any(p in low for p in dangerous):
            return ScriptRiskLevel.HIGH
        if any(p in low for p in ["userdel", "service stop", "Stop-Service", "kill -9"]):
            return ScriptRiskLevel.MEDIUM
        return ScriptRiskLevel.SAFE
