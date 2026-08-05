"""RemediationKnowledgeBase.query_vector_similar 词袋检索。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from remediator.remediation.knowledge_base import RemediationKnowledgeBase
from remediator.remediation.models import ErrorCategory, KnowledgeRecord


def test_query_vector_similar_orders_by_cosine():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "kb.db"
        kb = RemediationKnowledgeBase(p)
        base = KnowledgeRecord(
            error_category=ErrorCategory.COMMAND_NOT_FOUND,
            original_command="mvn clean",
            fixed_command="sudo apt install -y maven",
            root_cause="missing mvn",
            stderr_snippet="mvn: command not found",
        )
        kb.save_success(base)
        kb.save_success(
            KnowledgeRecord(
                error_category=ErrorCategory.COMMAND_NOT_FOUND,
                original_command="gradle build",
                fixed_command="sudo apt install -y gradle",
                root_cause="missing gradle",
                stderr_snippet="gradle: command not found",
            )
        )
        ranked = kb.query_vector_similar(
            query_text="mvn: command not found",
            query_command="mvn -v",
            error_category=ErrorCategory.COMMAND_NOT_FOUND,
            k=2,
        )
        assert ranked
        assert "mvn" in ranked[0][0].original_command
