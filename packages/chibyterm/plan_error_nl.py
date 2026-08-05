"""计划步骤 step_command_result / verification：基于 remediator 解析生成自然语言摘要（右侧卡片）。"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _suggestion_cn(se: Any) -> str:
    """按 ErrorCategory 给出简短中文处置建议（无需 LLM）。"""
    try:
        from remediator.remediation.models import ErrorCategory
    except Exception:
        return "请对照左侧终端输出核对环境与命令后再试。"

    cat = getattr(se, "error_category", None)
    pkg = getattr(se, "requires_package", None)

    if cat == ErrorCategory.FILE_NOT_FOUND or cat == ErrorCategory.FILE_NOT_FOUND_PATH_TYPO:
        return "检查路径拼写与文件名大小写，或先创建/挂载目标路径后再试。"
    if cat == ErrorCategory.PATH_ERROR:
        return "核对路径是否存在非法字符、相对/绝对路径是否与本机工作目录一致。"
    if cat in (ErrorCategory.PERMISSION_DENIED, ErrorCategory.PERMISSION_DENIED_SUDO):
        return "尝试使用有权限的用户/sudo（若策略允许），或改写到当前用户可写目录。"
    if cat in (ErrorCategory.COMMAND_NOT_FOUND, ErrorCategory.COMMAND_NOT_FOUND_PKG_MISSING):
        if pkg:
            return f"安装对应软件包或工具（例如包名线索：{pkg}），并确认 PATH 中包含该命令。"
        return "安装缺失依赖或确认命令已加入 PATH；容器内可用对应包管理器安装。"
    if cat == ErrorCategory.NETWORK or cat == ErrorCategory.NETWORK_TIMEOUT_UNREACHABLE:
        return "检查网络连通性、防火墙、代理与 DNS；确认目标主机与端口可达。"
    if cat == ErrorCategory.SYNTAX:
        return "检查脚本/命令语法与引号转义；先在交互 shell 中单步验证。"
    if cat == ErrorCategory.SERVER_UNAVAILABLE:
        return "确认目标服务已启动且监听正确端口；必要时查看服务端日志。"
    if cat == ErrorCategory.HARDWARE:
        return "疑似磁盘/硬件告警：请在主机侧检查磁盘空间、inode、硬件状态。"
    return "请查看左侧终端完整输出，修正命令或环境后重试。"


def nl_enrichment_for_plan_ws(
    *,
    command: str,
    output_tail: str,
    status: str,
    kind: str,
) -> Dict[str, str]:
    """
    为 WebSocket 负载追加 nl_title / nl_reason / nl_suggestion（及 structured_category）。

    kind: step_command_result | verification
    status: fail | pass | unknown | policy_denied
    """
    if status == "policy_denied":
        if kind == "verification":
            return {
                "nl_title": "❌ 验证命令未获策略放行",
                "nl_reason": "策略网关拒绝了验证命令的执行，终端侧未实际下发该验证命令。",
                "nl_suggestion": "请在策略中放行该验证命令，或改写为等价且已允许的探测方式。",
                "structured_category": "policy_denied",
            }
        return {
            "nl_title": "❌ 命令未获策略放行",
            "nl_reason": "策略网关拒绝了本步命令的执行，终端侧未实际下发。",
            "nl_suggestion": "请在策略配置中允许该命令，或改写为等价的已放行命令后再试。",
            "structured_category": "policy_denied",
        }

    if status != "fail":
        return {}

    title = "❌ 验证未通过" if kind == "verification" else "❌ 本步执行失败"

    try:
        from remediator.remediation.parser import parse_execution_error

        tail = (output_tail or "").strip()
        se = parse_execution_error(
            command=(command or "").strip(),
            return_code=1,
            stdout="",
            stderr=tail,
        )
        reason = (se.reason or "").strip() or se.display_type_cn
        path = getattr(se, "path", None)
        if path and path not in reason:
            reason = f"{reason}（涉及路径：{path}）"
        suggestion = _suggestion_cn(se)
        out: Dict[str, str] = {
            "nl_title": title,
            "nl_reason": reason,
            "nl_suggestion": suggestion,
            "structured_category": str(se.error_category.value),
        }
        return out
    except Exception as e:
        logger.debug("plan_error_nl parse_execution_error skipped: %s", e)
        tail = (output_tail or "").strip()
        return {
            "nl_title": title,
            "nl_reason": (tail[:800] if tail else "输出中存在错误或命令未成功完成（启发式判定）。"),
            "nl_suggestion": "请查看左侧终端完整输出，核对路径、权限与依赖后重试。",
            "structured_category": "unknown",
        }


def merge_nl_payload(payload: Dict[str, Any], *, command: str, output_tail: str, status: str, kind: str) -> None:
    """就地合并 nl_* 字段（仅失败等场景有内容）。"""
    extra = nl_enrichment_for_plan_ws(
        command=command,
        output_tail=output_tail,
        status=status,
        kind=kind,
    )
    if extra:
        payload.update(extra)
