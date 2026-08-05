"""Healing — 统一知识检索器。

从两个知识源同时检索：
1. KnowledgeHub (chibycore/knowledge_hub) — 语义检索 + 结构化条目
2. RemediationKB (remediator/remediation) — 指纹命中 + 模糊匹配

结果合并、去重、置信度评分、排序后返回。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from chibycore.healing.confidence import (
    HealingConfidence,
    ConfidenceLevel,
    score_confidence,
)

logger = logging.getLogger(__name__)


@dataclass
class RetrievedKnowledge:
    """从知识库检索到的一条修复知识。"""
    # 核心修复信息
    remediation: str                                    # 修复命令
    original_command: Optional[str] = None              # 触发修复的原始命令
    root_cause: Optional[str] = None                    # 根因分析
    # 匹配信息
    confidence: HealingConfidence = field(default_factory=lambda: HealingConfidence(score=0.0, level=ConfidenceLevel.LOW, match_level="tag_context", reason="default"))  # type: ignore
    # 来源追踪
    source: str = "unknown"                             # knowledge_hub / remediation_kb
    source_id: Optional[str] = None                     # entry id / case id
    # 上下文
    tags: List[str] = field(default_factory=list)
    applicable_os: List[str] = field(default_factory=list)
    error_category: Optional[str] = None
    # 元数据
    success_count: int = 0
    failure_count: int = 0
    created_at: Optional[str] = None


# ── 检索器 ───────────────────────────────────────────────────────────────


class HealingKnowledgeRetriever:
    """统一知识检索器。

    使用方式：
        retriever = HealingKnowledgeRetriever()
        results = retriever.retrieve(
            command="apt install nginx",
            stderr="E: Package 'nginx' has no installation candidate",
            stdout="",
            limit=5,
        )
        if results and results[0].confidence.level == ConfidenceLevel.HIGH:
            apply_remediation(results[0].remediation)
    """

    def __init__(self) -> None:
        self._kh_searcher: Any = None  # KnowledgeHubSearch（懒加载）
        self._rk_base: Any = None      # RemediationKnowledgeBase（懒加载）

    # ── 懒加载 ──────────────────────────────────────────────────────────

    def _ensure_kh_search(self) -> Any:
        if self._kh_searcher is not None:
            return self._kh_searcher
        try:
            from chibycore.knowledge_hub.search import KnowledgeHubSearch
            self._kh_searcher = KnowledgeHubSearch()
        except Exception as ex:
            logger.warning("KnowledgeHubSearch 加载失败: %s", ex)
            self._kh_searcher = _EmptySearcher()
        return self._kh_searcher

    def _ensure_rk_base(self) -> Any:
        if self._rk_base is not None:
            return self._rk_base
        try:
            from remediator.remediation.knowledge_base import RemediationKnowledgeBase
            from pathlib import Path
            # 默认路径：项目根目录 data/remediation_kb.db
            root = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root().parent
            db_path = root / "data" / "remediation_kb.db"
            if db_path.exists():
                self._rk_base = RemediationKnowledgeBase(db_path)
            else:
                self._rk_base = _EmptySearcher()
        except Exception as ex:
            logger.warning("RemediationKnowledgeBase 加载失败: %s", ex)
            self._rk_base = _EmptySearcher()
        return self._rk_base

    # ── 主检索 ──────────────────────────────────────────────────────────

    def retrieve(
        self,
        command: str,
        stderr: str = "",
        stdout: str = "",
        error_category: Optional[str] = None,
        limit: int = 5,
    ) -> List[RetrievedKnowledge]:
        """
        统一检索入口。

        参数：
            command:      原始命令
            stderr:       错误输出（主要匹配依据）
            stdout:       标准输出（辅助）
            error_category: 可选错误类别筛选
            limit:        最大返回条数

        返回：
            按置信度降序排列的 RetrievedKnowledge 列表
        """
        all_results: List[RetrievedKnowledge] = []

        # 1. 从 KnowledgeHub 检索
        try:
            kh_results = self._search_knowledge_hub(command, stderr, limit * 2)
            all_results.extend(kh_results)
        except Exception as ex:
            logger.warning("KnowledgeHub 检索失败: %s", ex)

        # 2. 从 RemediationKB 检索
        try:
            rk_results = self._search_remediation_kb(command, stderr, error_category)
            all_results.extend(rk_results)
        except Exception as ex:
            logger.warning("RemediationKB 检索失败: %s", ex)

        # 3. 去重（按 remediation 命令去重，保留置信度更高的那条）
        seen: Dict[str, RetrievedKnowledge] = {}
        for r in all_results:
            key = (r.remediation or "").strip().lower()
            if not key:
                continue
            if key in seen:
                if r.confidence.score > seen[key].confidence.score:
                    seen[key] = r
            else:
                seen[key] = r

        # 4. 排序
        sorted_results = sorted(
            seen.values(),
            key=lambda x: x.confidence.score,
            reverse=True,
        )
        return sorted_results[:limit]

    # ── KnowledgeHub 检索 ──────────────────────────────────────────────

    def _search_knowledge_hub(
        self,
        command: str,
        stderr: str,
        limit: int,
    ) -> List[RetrievedKnowledge]:
        """通过 KnowledgeHubSearch 检索语义匹配。"""
        searcher = self._ensure_kh_search()

        from chibycore.knowledge_hub.models import SearchQuery

        query_text = f"{command} {stderr}".strip()[:500] or command[:200]
        sq = SearchQuery(q=query_text, mode="kb", limit=limit)
        resp = searcher.search(sq)

        results: List[RetrievedKnowledge] = []
        storage = getattr(searcher, "_storage", None)

        for sr in resp.results[:limit]:
            # 回查完整 KBEntry 以获取 remediation 等字段
            entry = None
            if storage:
                try:
                    entry = storage.get_kb_entry(sr.entry_id)
                except Exception:
                    pass

            remediation = (entry.remediation if entry else "").strip()
            if not remediation:
                # 没有 remediation 的条目无法用于修复
                continue

            conf = score_confidence(
                query_command=command,
                query_stderr=stderr,
                entry_command=(entry.original_command if entry else command),
                entry_remediation=remediation,
                entry_fingerprint=(entry.error_fingerprint if entry else None),
                entry_category=(entry.category.value if entry else None),
                entry_source=(entry.source if entry else "knowledge_hub"),
                entry_confidence_field=(entry.confidence.value if entry else None),
                entry_success_count=(entry.success_count if entry else 0),
                entry_tags=(entry.tags if entry else []),
                entry_symptom=(entry.symptom if entry else None),
                exact_fingerprint_match=False,
                category_package_match=False,
            )

            results.append(RetrievedKnowledge(
                remediation=remediation,
                original_command=(entry.original_command if entry else command),
                root_cause=(entry.root_cause if entry else None),
                confidence=conf,
                source="knowledge_hub",
                source_id=sr.entry_id,
                tags=getattr(entry, "tags", []) or [],
                applicable_os=getattr(entry, "applicable_os", []) or [],
                error_category=(entry.category.value if entry else None),
                success_count=(entry.success_count if entry else 0),
                failure_count=(entry.failure_count if entry else 0),
            ))

        return results

    # ── RemediationKB 检索 ─────────────────────────────────────────────

    def _search_remediation_kb(
        self,
        command: str,
        stderr: str,
        error_category: Optional[str] = None,
    ) -> List[RetrievedKnowledge]:
        """通过 RemediationKnowledgeBase 检索指纹/模糊匹配。"""
        rk = self._ensure_rk_base()

        # 尝试用 query_best_match 获取最佳匹配
        try:
            # 构造 structured error
            from remediator.remediation.models import (
                StructuredError,
                ErrorCategory,
                EnvironmentSnapshot,
            )

            cat = ErrorCategory.OTHER
            if error_category:
                try:
                    cat = ErrorCategory(error_category)
                except ValueError:
                    pass

            err = StructuredError(
                error_category=cat,
                stderr_snippet=stderr[:2000],
                raw_stderr=stderr,
                metadata={"command": command},
                requires_package=None,
            )
            env = EnvironmentSnapshot(
                os_name="linux",
                os_version="",
                current_user="root",
                is_root_or_sudo=True,
                home_dir="/root",
                path_separator="/",
            )

            match = rk.query_best_match(err, env)
        except Exception:
            match = None

        results: List[RetrievedKnowledge] = []
        if match:
            conf = score_confidence(
                query_command=command,
                query_stderr=stderr,
                entry_command=match.original_command,
                entry_remediation=match.fixed_command,
                entry_fingerprint=match.fingerprint,
                entry_source="remediator",
                entry_success_count=0,
                exact_fingerprint_match=True,
            )
            results.append(RetrievedKnowledge(
                remediation=match.fixed_command,
                original_command=match.original_command,
                root_cause=match.root_cause,
                confidence=conf,
                source="remediation_kb",
                source_id=match.fingerprint,
                error_category=match.error_category.value if match.error_category else None,
            ))

        return results


class _EmptySearcher:
    """空对象，当依赖未安装时静默降级。"""

    def search(self, *args, **kwargs) -> Any:
        from chibycore.knowledge_hub.models import SearchResponse, SearchResult
        return SearchResponse(
            query="", total=0, results=[], mode="kb", took_ms=0
        )

    def query_best_match(self, *args, **kwargs) -> None:
        return None

    def get_kb_entry(self, *args, **kwargs) -> None:
        return None

    def find_similar(self, *args, **kwargs) -> list:
        return []
