"""KnowledgeHub — 语义检索引擎。

三层检索策略：
1. 关键词精确匹配（LIKE + 标签匹配）
2. 文本相似度（TF-IDF / 余弦相似度，无需外部 embedding）
3. 多路召回 + 排序（综合评分）

当前实现：轻量级 token 频率 + 余弦相似度（无外部依赖）。
升级路径：后续接入 pgvector / Milvus / Qdrant 进行向量检索。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from chibycore.knowledge_hub.models import (
    KBEntry,
    KBCategory,
    KBConfidence,
    ScriptEntry,
    BestPractice,
    SearchQuery,
    SearchResult,
    SearchResponse,
)
from chibycore.knowledge_hub.storage import KnowledgeHubStorage, _cosine_sim, _token_freq, _levenshtein_ratio

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 检索器
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeHubSearch:
    """
    统一检索入口。

    使用场景：
    - 用户在知识库 Tab 搜索历史经验
    - Agent 执行前先查知识库（命中则复用，避免重复 LLM 调用）
    - 脚本推荐：根据用户描述推荐最合适的脚本
    """

    def __init__(self, storage: Optional[KnowledgeHubStorage] = None) -> None:
        self._storage = storage or KnowledgeHubStorage.get_instance()

    # ── 统一检索 ─────────────────────────────────────────────────────────────

    def search(self, query: SearchQuery) -> SearchResponse:
        """
        主检索入口。根据 mode 决定搜索范围。
        """
        t0 = time.time()

        if query.mode == "kb":
            results = self._search_kb(query)
        elif query.mode == "script":
            results = self._search_script(query)
        elif query.mode == "best_practice":
            results = self._search_bp(query)
        else:  # "all"
            results = (
                self._search_kb(query)
                + self._search_script(query)
                + self._search_bp(query)
            )
            # 全局排序
            results.sort(key=lambda r: r.score, reverse=True)
            results = results[: query.limit]

        took_ms = int((time.time() - t0) * 1000)
        return SearchResponse(
            query=query.q,
            total=len(results),
            results=results,
            mode=query.mode,
            took_ms=took_ms,
        )

    # ── KB 检索 ──────────────────────────────────────────────────────────────

    def _search_kb(self, query: SearchQuery) -> List[SearchResult]:
        """检索知识库条目。"""
        # 过滤
        kb_filtered = self._filter_kb(query)
        if not kb_filtered:
            return []

        q_tokens = _token_freq(query.q)
        q_lower = query.q.lower()

        scored: List[Tuple[float, KBEntry]] = []
        for entry in kb_filtered:
            score = self._score_kb_entry(entry, q_tokens, q_lower)
            if score >= 0.1:  # 阈值过滤
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, entry in scored[: query.limit]:
            results.append(self._kb_to_result(entry, score, query.q))
        return results

    def _filter_kb(self, query: SearchQuery) -> List[KBEntry]:
        """预过滤：类别、标签、OS、置信度。"""
        entries = self._storage.list_kb_entries(
            category=query.category.value if query.category else None,
            limit=500,
        )
        # 内存过滤
        if query.tags:
            entries = [e for e in entries if any(t in e.tags for t in query.tags)]
        if query.applicable_os:
            entries = [e for e in entries if query.applicable_os in e.applicable_os]
        if query.min_confidence:
            confidence_order = {"low": 0, "medium": 1, "high": 2}
            min_level = confidence_order.get(query.min_confidence.value, 0)
            entries = [
                e
                for e in entries
                if confidence_order.get(e.confidence.value, 0) >= min_level
            ]
        if query.min_rating is not None:
            entries = [e for e in entries if e.rating >= query.min_rating]
        return entries

    def _score_kb_entry(
        self, entry: KBEntry, q_tokens: Dict[str, float], q_lower: str
    ) -> float:
        """综合评分：文本相似度 + 命中关键词 + 成功率。"""
        # 多字段 token 相似度（跳过 None / 字面量 "None"）
        def _t(v: object) -> str:
            if v is None:
                return ""
            s = str(v).strip()
            return "" if not s or s.lower() == "none" else s

        text_all = " ".join(
            [
                _t(entry.title),
                _t(entry.symptom),
                _t(entry.root_cause),
                _t(entry.remediation),
            ]
        )
        entry_tokens = _token_freq(text_all)
        text_sim = _cosine_sim(q_tokens, entry_tokens)

        # 关键词精确命中（加分）
        bonus = 0.0
        title_lower = _t(entry.title).lower()
        symptom_lower = _t(entry.symptom).lower()
        # 症状中含查询词
        for kw in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_/]{3,}", q_lower):
            if kw in symptom_lower:
                bonus += 0.15
            if kw in title_lower:
                bonus += 0.2

        # 成功率加权
        total = entry.success_count + entry.failure_count
        success_rate = entry.success_count / total if total > 0 else 0.5
        rating_score = entry.rating / 5.0 if entry.rating > 0 else 0.5

        # 置信度加权
        conf_score = {"high": 1.0, "medium": 0.8, "low": 0.5}.get(entry.confidence.value, 0.8)

        return min(1.0, text_sim * 0.5 + bonus + success_rate * 0.2 + rating_score * 0.1 + conf_score * 0.2)

    def _kb_to_result(self, entry: KBEntry, score: float, query: str) -> SearchResult:
        """KBEntry → SearchResult"""
        def _t(v: object) -> str:
            if v is None:
                return ""
            s = str(v).strip()
            return "" if not s or s.lower() == "none" else s

        parts = [p for p in (_t(entry.symptom), _t(entry.remediation)) if p]
        snippet = self._make_snippet(" | ".join(parts) if parts else _t(entry.title), query)
        return SearchResult(
            entry_type="kb",
            entry_id=entry.id,
            title=_t(entry.title) or entry.id,
            snippet=snippet,
            score=round(score, 3),
            category=entry.category.value,
            tags=entry.tags,
            confidence=entry.confidence.value,
            applicable_os=entry.applicable_os,
            use_count=entry.success_count,
            success_rate=(
                entry.success_count / (entry.success_count + entry.failure_count)
                if (entry.success_count + entry.failure_count) > 0
                else None
            ),
        )

    # ── 脚本检索 ─────────────────────────────────────────────────────────────

    def _search_script(self, query: SearchQuery) -> List[SearchResult]:
        """检索脚本库。"""
        scripts = self._storage.list_script_entries(
            category=query.category.value if query.category else None,
            limit=500,
        )
        if not scripts:
            return []

        q_tokens = _token_freq(query.q)
        q_lower = query.q.lower()

        scored: List[Tuple[float, ScriptEntry]] = []
        for script in scripts:
            score = self._score_script_entry(script, q_tokens, q_lower)
            if score >= 0.1:
                scored.append((score, script))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, script in scored[: query.limit]:
            results.append(self._script_to_result(script, score, query.q))
        return results

    def _score_script_entry(
        self, script: ScriptEntry, q_tokens: Dict[str, float], q_lower: str
    ) -> float:
        """脚本评分：名称/描述匹配 + 使用次数。"""
        text_all = " ".join([script.name, script.description, script.content[:500]])
        entry_tokens = _token_freq(text_all)
        text_sim = _cosine_sim(q_tokens, entry_tokens)

        bonus = 0.0
        name_lower = script.name.lower()
        desc_lower = script.description.lower()
        for kw in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_]{3,}", q_lower):
            if kw in name_lower:
                bonus += 0.25
            if kw in desc_lower:
                bonus += 0.1

        total = script.success_count + script.failure_count
        success_rate = script.success_count / total if total > 0 else 0.5
        use_score = min(1.0, script.use_count / 100.0)

        return min(1.0, text_sim * 0.5 + bonus + success_rate * 0.2 + use_score * 0.1)

    def _script_to_result(self, script: ScriptEntry, score: float, query: str) -> SearchResult:
        """ScriptEntry → SearchResult"""
        snippet = self._make_snippet(script.description, query)
        return SearchResult(
            entry_type="script",
            entry_id=script.id,
            title=script.name,
            snippet=snippet,
            score=round(score, 3),
            category=script.category.value,
            tags=script.tags,
            language=script.language.value,
            risk_level=script.risk_level.value,
            applicable_os=script.applicable_os,
            use_count=script.use_count,
            success_rate=(
                script.success_count / (script.success_count + script.failure_count)
                if (script.success_count + script.failure_count) > 0
                else None
            ),
        )

    # ── 最佳实践检索 ─────────────────────────────────────────────────────────

    def _search_bp(self, query: SearchQuery) -> List[SearchResult]:
        """检索最佳实践。"""
        bps = self._storage.list_best_practices(
            category=query.category.value if query.category else None,
            limit=500,
        )
        if not bps:
            return []

        q_tokens = _token_freq(query.q)
        scored: List[Tuple[float, BestPractice]] = []
        for bp in bps:
            text_all = " ".join([bp.title, bp.description, bp.steps[:500]])
            entry_tokens = _token_freq(text_all)
            score = _cosine_sim(q_tokens, entry_tokens)
            if score >= 0.1:
                scored.append((score, bp))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, bp in scored[: query.limit]:
            results.append(self._bp_to_result(bp, score, query.q))
        return results

    def _bp_to_result(self, bp: BestPractice, score: float, query: str) -> SearchResult:
        return SearchResult(
            entry_type="best_practice",
            entry_id=bp.id,
            title=bp.title,
            snippet=self._make_snippet(bp.description, query),
            score=round(score, 3),
            category=bp.category.value,
            tags=bp.tags,
            applicable_os=bp.applicable_os,
        )

    # ── 工具方法 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _make_snippet(text: str, query: str, context_chars: int = 120) -> str:
        """
        抽取包含查询关键词的文本片段，并高亮关键词。
        返回纯文本（前端自行渲染高亮）。
        """
        if not text or not query:
            return text[:context_chars] if text else ""

        # 找最短匹配位置
        q_words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z0-9_/]{3,}", query.lower())
        text_lower = text.lower()
        best_pos = -1
        for w in q_words:
            pos = text_lower.find(w)
            if pos != -1 and (best_pos == -1 or pos < best_pos):
                best_pos = pos

        if best_pos == -1:
            return text[:context_chars]

        start = max(0, best_pos - context_chars // 2)
        end = min(len(text), start + context_chars)
        snippet = text[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(text):
            snippet = snippet + "…"
        return snippet

    # ── 上下文推荐 ─────────────────────────────────────────────────────────────

    def suggest_related_kb(
        self, error_fingerprint: Optional[str] = None, category: Optional[KBCategory] = None, limit: int = 3
    ) -> List[SearchResult]:
        """根据错误指纹或类别推荐相关 KB（用于 Agent 执行前预热）。"""
        if error_fingerprint:
            entries = self._storage.list_kb_entries(limit=200)
            matched = [e for e in entries if e.error_fingerprint == error_fingerprint]
            if matched:
                return [self._kb_to_result(e, 1.0, error_fingerprint) for e in matched[:limit]]

        if category:
            entries = self._storage.list_kb_entries(
                category=category.value, limit=limit
            )
            return [
                self._kb_to_result(e, 0.9, category.value) for e in entries[:limit]
            ]

        return []
