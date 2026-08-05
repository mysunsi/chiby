"""
Phase 7.2：故障诊断 Markdown 报告（Human-in-the-Loop）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from remediator.remediation.models import EnvironmentSnapshot, StructuredError

logger = logging.getLogger(__name__)

OutcomeLabel = Literal["Success", "Failed", "Blocked"]


@dataclass
class DiagnosticBundle:
    """单次会话的诊断素材（由 executor_wrapper 组装）。"""

    session_id: str
    original_command: str
    duration_ms: int
    outcome: OutcomeLabel
    env: EnvironmentSnapshot
    termination: str = ""
    structured_initial: Optional[StructuredError] = None
    root_cause_text: Optional[str] = None
    history_arrow_chain: Optional[str] = None
    kb_hit: bool = False
    llm_calls: int = 0
    fix_type: str = ""
    dry_run_report: Optional[Dict[str, Any]] = None
    blocked_detail: Optional[str] = None
    session_message: Optional[str] = None
    extra_lines: List[str] = field(default_factory=list)


def _structured_section(se: StructuredError) -> str:
    lines = [
        f"- **error_category**: `{se.error_category.value}`",
        f"- **return_code**: `{se.return_code}`",
        f"- **reason**: {se.reason or '（无）'}",
    ]
    if se.path:
        lines.append(f"- **path**: `{se.path}`")
    if se.requires_package:
        lines.append(f"- **requires_package**: `{se.requires_package}`")
    snip = (se.stderr_snippet or se.raw_stderr or "").strip()
    if snip:
        lines.append("- **stderr（摘录）**:")
        lines.append("")
        lines.append("```text")
        lines.append(snip[:4000])
        lines.append("```")
    return "\n".join(lines)


def _decision_section(b: DiagnosticBundle) -> str:
    parts: List[str] = []
    if b.fix_type == "lite":
        parts.append("- **路径**: **Lite Fix**（轻量级规则修复，未调用 LLM）")
    elif b.kb_hit:
        parts.append("- **路径**: **Knowledge Base**（本地知识库命中优先策略）")
    elif b.llm_calls > 0:
        parts.append(
            f"- **路径**: **LLM**（大模型推理；本轮会话记录到的调用次数: **{b.llm_calls}**）"
        )
    elif b.outcome == "Blocked":
        parts.append("- **路径**: **策略拦截**（未进入自动修正闭环）")
    elif b.outcome == "Success" and not b.fix_type and not b.kb_hit and not b.llm_calls:
        parts.append("- **路径**: **无需修正**（初次执行即成功，或未触发 KB/Lite/LLM）")
    else:
        parts.append("- **路径**: 规则引擎与 Controller 闭环（详见终止原因与历史链）")
    if b.termination:
        parts.append(f"- **termination**: `{b.termination}`")
    return "\n".join(parts)


def _dry_run_section(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    st = report.get("structured") or {}
    if st:
        lines.append("**Dry-run 结构化摘要（节选）**:")
        lines.append("")
        lines.append("```json")
        try:
            lines.append(json.dumps(st, ensure_ascii=False, indent=2)[:8000])
        except Exception:
            lines.append(str(st)[:8000])
        lines.append("```")
    prop = report.get("proposal")
    if prop:
        lines.append("")
        lines.append("**proposal（节选）**:")
        lines.append("")
        lines.append("```json")
        try:
            lines.append(json.dumps(prop, ensure_ascii=False, indent=2)[:8000])
        except Exception:
            lines.append(str(prop)[:8000])
        lines.append("```")
    return "\n".join(lines)


def render_diagnostic_markdown(bundle: DiagnosticBundle) -> str:
    """生成 Markdown 正文。"""
    title = f"# 故障诊断报告 · `{bundle.session_id}`"
    summary = "\n".join(
        [
            "## 摘要",
            "",
            f"- **原始命令**: `{bundle.original_command}`",
            f"- **最终结果**: **{bundle.outcome}**",
            f"- **耗时**: {bundle.duration_ms} ms",
            "",
        ]
    )

    root = ["## 根因分析", ""]
    if bundle.root_cause_text:
        root.append(f"**root_cause（文案）**: {bundle.root_cause_text}")
        root.append("")
    if bundle.session_message:
        root.append(f"**会话 message**: {bundle.session_message}")
        root.append("")
    if bundle.structured_initial:
        root.append("**StructuredError（初始探测）**:")
        root.append("")
        root.append(_structured_section(bundle.structured_initial))
        root.append("")
    elif bundle.dry_run_report:
        root.append(_dry_run_section(bundle.dry_run_report))
        root.append("")
    else:
        root.append("（本轮未持久化 StructuredError 对象；请参考会话终止信息与历史链。）")
        root.append("")

    traj = "## 修复轨迹", "", ""
    if bundle.history_arrow_chain:
        traj = (
            "## 修复轨迹",
            "",
            "```text",
            bundle.history_arrow_chain,
            "```",
            "",
        )
    else:
        traj = ("## 修复轨迹", "", "（无历史链记录）", "")

    decision = "## 决策依据", "", _decision_section(bundle), ""

    env_sec = "\n".join(
        [
            "## 环境快照",
            "",
            f"- **OS**: {bundle.env.os_name} {bundle.env.os_version}".strip(),
            f"- **CWD**: `{bundle.env.cwd}`",
            f"- **User**: `{bundle.env.current_user}`",
            f"- **Shell**: `{bundle.env.shell}`",
            "",
        ]
    )

    extra = ""
    if bundle.extra_lines:
        extra = "## 备注\n\n" + "\n".join(bundle.extra_lines) + "\n\n"

    blocked = ""
    if bundle.blocked_detail:
        blocked = "## 拦截说明\n\n" + bundle.blocked_detail + "\n\n"

    parts = [
        title,
        "",
        summary,
        "\n".join(root),
        "\n".join(traj),
        "\n".join(decision),
        blocked,
        env_sec,
        extra,
        "---",
        "",
        "*由 ai-ops-assistant diagnostics 模块自动生成。*",
        "",
    ]
    return "\n".join(parts)


def write_diagnostic_report(
    bundle: DiagnosticBundle,
    *,
    reports_dir: Optional[Path] = None,
) -> Optional[Path]:
    """
    写入 ``reports_dir / {session_id}.md``。

    写入失败时记录日志并返回 ``None``，不向外抛。
    """
    base = reports_dir if reports_dir is not None else Path("reports")
    try:
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{bundle.session_id}.md"
        path.write_text(render_diagnostic_markdown(bundle), encoding="utf-8")
        return path
    except OSError as e:
        logger.warning("诊断报告写入失败（已忽略）: %s", e)
        return None


__all__ = [
    "DiagnosticBundle",
    "OutcomeLabel",
    "render_diagnostic_markdown",
    "write_diagnostic_report",
]
