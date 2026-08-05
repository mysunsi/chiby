"""为 propose_remediation 拼装 KB 向量命中 + few-shot 后缀。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .few_shot_templates import FEW_SHOT_REMEDIATION_BLOCK

if TYPE_CHECKING:
    from .knowledge_base import RemediationKnowledgeBase
    from .models import EnvironmentSnapshot, StructuredError


def build_remediation_prompt_suffix(
    kb: RemediationKnowledgeBase,
    structured_error: StructuredError,
    env: EnvironmentSnapshot,
    *,
    max_cases: int = 4,
) -> str:
    _ = env  # 预留：未来可按 OS 过滤 KB
    lines = [FEW_SHOT_REMEDIATION_BLOCK.strip()]
    qstderr = structured_error.stderr_snippet or structured_error.raw_stderr or ""
    qcmd = (structured_error.metadata.get("command") or "").strip()
    ranked = kb.query_vector_similar(
        query_text=qstderr,
        query_command=qcmd,
        error_category=structured_error.error_category,
        k=max_cases,
    )
    if ranked:
        lines.append("\n【KB 向量检索（词袋余弦）相似案例】")
        for i, (rec, score) in enumerate(ranked, 1):
            lines.append(
                f"{i}. sim={score:.2f} | 类型={rec.error_category.value} | 根因: {(rec.root_cause or '')[:140]}"
            )
            lines.append(f"   原: {(rec.original_command or '')[:200]}")
            lines.append(f"   修: {(rec.fixed_command or '')[:240]}")
    return "\n".join(lines)
