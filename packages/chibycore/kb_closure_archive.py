"""成功闭环占位归档：追加 JSONL + 写入 KnowledgeHub，便于后续接入真实知识库/向量库。"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from chibycore.closure_service import ClosurePayload
from chibycore.knowledge_hub.models import KBEntry, KBCategory, KBConfidence
from chibycore.knowledge_hub.storage import KnowledgeHubStorage

logger = logging.getLogger(__name__)


# ── JSONL 归档 ────────────────────────────────────────────────────────────


def _kb_path() -> Path:
    root = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "kb_closure_archive.jsonl"


def _auto_extract_title(cmd: str, stderr: str, stdout: str) -> str:
    """从命令+错误信息自动生成知识标题。"""
    # 提取主要命令名
    cmd_stripped = (cmd or "").strip().split(maxsplit=1)
    cmd_name = cmd_stripped[0] if cmd_stripped else "unknown"
    # 从 stderr 提取关键错误片段
    err = (stderr or "")[:200].replace("\n", " ").strip()
    out = (stdout or "")[:200].replace("\n", " ").strip()
    brief = err or out
    if brief:
        return f"[{cmd_name}] {brief[:120]}"
    return f"[{cmd_name}] 执行成功"


def _infer_category(cmd: str) -> KBCategory:
    """根据命令推断知识类别。"""
    cmd_lower = cmd.lower().strip()
    # 包管理
    if any(kw in cmd_lower for kw in ("apt", "yum", "dnf", "pip", "npm", "brew", "choco", "pkg", "apk", "install", "remove", "uninstall")):
        return KBCategory.PACKAGE_MANAGEMENT
    # 服务管理
    if any(kw in cmd_lower for kw in ("systemctl", "service", "supervisor", "nginx", "apache", "mysqld", "docker", "kubectl", "podman")):
        if "docker" in cmd_lower or "kubectl" in cmd_lower or "podman" in cmd_lower:
            return KBCategory.DOCKER_K8S
        return KBCategory.SERVICE_OPS
    # 网络
    if any(kw in cmd_lower for kw in ("curl", "wget", "ping", "netstat", "ss ", "ifconfig", "ip ", "nslookup", "dig", "traceroute", "netcat", "nc ", "iptables", "ufw")):
        return KBCategory.NETWORK_OPS
    # 用户管理
    if any(kw in cmd_lower for kw in ("user", "passwd", "chown", "chmod", "usermod", "group", "sudo")):
        return KBCategory.USER_MANAGEMENT
    # 数据库
    if any(kw in cmd_lower for kw in ("mysql", "psql", "sqlite", "mongodb", "redis-cli", "pg_dump", "pg_restore")):
        return KBCategory.DATABASE
    # 监控
    if any(kw in cmd_lower for kw in ("top", "htop", "ps ", "vmstat", "iostat", "df ", "du ", "free", "journalctl", "dmesg", "tail", "grep")):
        return KBCategory.SYSTEM_MONITOR
    # 安全
    if any(kw in cmd_lower for kw in ("ssh", "scp", "rsync", "openssl", "gpg", "key", "cert", "tls", "ssl", "auth", "permission", "denied")):
        return KBCategory.SECURITY
    # 故障恢复（含 stderr 的错误类关键词）
    if any(kw in cmd_lower for kw in ("error", "fail", "fatal", "crash", "timeout", "refused", "not found")):
        return KBCategory.FAILURE_RECOVERY
    return KBCategory.OTHER


# ── KnowledgeHub 写入 ─────────────────────────────────────────────────────


def _write_to_knowledge_hub(cp: ClosurePayload, *, judge_reason: str = "", trace_id: str = "") -> None:
    """将成功闭环写入 KnowledgeHub，供后续检索复用。"""
    try:
        storage = KnowledgeHubStorage.get_instance()
        effective_cmd = cp.effective_command or cp.raw_command or ""
        stderr = cp.stderr or ""
        stdout = cp.stdout or ""

        entry = KBEntry(
            title=_auto_extract_title(effective_cmd, stderr, stdout),
            category=_infer_category(effective_cmd),
            symptom=(
                f"stderr: {(stderr[:1500]).replace(chr(10), '; ')}\n"
                f"stdout: {(stdout[:1500]).replace(chr(10), '; ')}"
            ),
            root_cause=judge_reason or "返回码异常触发修复",
            remediation=effective_cmd,
            verify_method=f"exit_code={cp.exit_code}",
            applicable_os=["linux"] if cp.transport in ("local", "ssh") else [],
            tags=_auto_tags(effective_cmd, stderr),
            error_fingerprint=None,  # 暂不计算指纹，后续可集成 compute_error_fingerprint
            original_command=cp.raw_command,
            confidence=KBConfidence.MEDIUM,
            source="terminal_session",
            source_id=trace_id or cp.trace_id,
            success_count=1,
        )
        storage.save_kb_entry(entry)
        logger.info(
            "KnowledgeHub 已写入闭环知识条目 id=%s cmd=%s",
            entry.id, effective_cmd[:80],
        )
    except Exception as ex:
        logger.warning("KnowledgeHub 写入失败（非致命）: %s", ex)


def _auto_tags(cmd: str, stderr: str) -> list[str]:
    """自动提取标签。"""
    tags: list[str] = []
    cmd_lower = cmd.lower()
    # 工具标签
    for tool in ("apt", "yum", "dnf", "pip", "npm", "docker", "kubectl",
                 "systemctl", "service", "curl", "wget", "ssh", "git",
                 "mysql", "python", "node", "nginx", "redis", "postgres",
                 "mongodb", "ufw", "iptables", "chmod", "chown"):
        if tool in cmd_lower:
            tags.append(tool)
    # 操作系统
    if any(kw in cmd_lower for kw in ("dnf", "yum", "rpm")):
        tags.append("rhel")
    if "apt" in cmd_lower or "dpkg" in cmd_lower:
        tags.append("debian")
    if "choco" in cmd_lower or "powershell" in cmd_lower:
        tags.append("windows")
    # 错误标签
    err_lower = (stderr or "").lower()
    for err_type in ("timeout", "connection refused", "not found", "permission denied",
                     "no such", "cannot", "failed", "unreachable"):
        if err_type in err_lower:
            tags.append(err_type.replace(" ", "_"))
    return tags


# ── 公开接口 ──────────────────────────────────────────────────────────────


def archive_closure_success(
    cp: ClosurePayload,
    *,
    judge_reason: str = "",
    trace_id: Optional[str] = None,
) -> None:
    """写入 JSONL 存档 + 同步写入 KnowledgeHub。"""
    row: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "trace_id": trace_id or cp.trace_id,
        "raw_command": cp.raw_command,
        "effective_command": cp.effective_command,
        "transport": cp.transport,
        "exit_code": cp.exit_code,
        "risk_level": getattr(cp.risk_level, "value", str(cp.risk_level)),
        "stdout_tail": (cp.stdout or "")[-8000:],
        "stderr_tail": (cp.stderr or "")[-8000:],
        "judge_reason": judge_reason,
        "session_id": cp.session_id,
        "plan_id": cp.plan_id,
        "nl_intent_hint": cp.nl_intent_hint,
    }
    path = _kb_path()
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as ex:
        logger.error("kb_closure_archive JSONL 写入失败: %s", ex)

    # 同步写入 KnowledgeHub（非致命）
    _write_to_knowledge_hub(
        cp,
        judge_reason=judge_reason,
        trace_id=trace_id or cp.trace_id,
    )
