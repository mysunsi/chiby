"""KnowledgeHub — FastAPI REST API 路由。

挂载方式（在 terminal/main.py 中）：
    from chibycore.knowledge_hub.api import router as knowledge_hub_router
    app.include_router(knowledge_hub_router, prefix="/api/kb", tags=["KnowledgeHub"])

路由说明：
  /api/kb/stats                    — 统计信息
  /api/kb/search                   — 检索（KB / 脚本 / 最佳实践）
  /api/kb/kb                       — KB 条目 CRUD
  /api/kb/scripts                  — 脚本库 CRUD
  /api/kb/best-practices          — 最佳实践 CRUD
  /api/kb/ingest                  — 手动沉淀（支持自动分类/指纹去重）
  /api/kb/export                  — 导出全部数据
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from chibycore.knowledge_hub import (
    KnowledgeHubSearch,
    KnowledgeHubStorage,
    KBEntry,
    KBConfidence,
    KBCategory,
    ScriptEntry,
    ScriptLanguage,
    ScriptRiskLevel,
    BestPractice,
    SearchQuery,
    IngestSource,
)
from chibycore.knowledge_hub.ingestion import KnowledgeIngester
from chibycore.knowledge_hub.models import PendingKBStatus

logger = logging.getLogger(__name__)
router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# 存储 & 检索单例（延迟初始化）
# ─────────────────────────────────────────────────────────────────────────────

_storage: Optional[KnowledgeHubStorage] = None
_search: Optional[KnowledgeHubSearch] = None
_ingester: Optional[KnowledgeIngester] = None


def _get_storage() -> KnowledgeHubStorage:
    global _storage
    if _storage is None:
        _storage = KnowledgeHubStorage.get_instance()
    return _storage


def _get_search() -> KnowledgeHubSearch:
    global _search
    if _search is None:
        _search = KnowledgeHubSearch(_get_storage())
    return _search


def _get_ingester() -> KnowledgeIngester:
    global _ingester
    if _ingester is None:
        _ingester = KnowledgeIngester(_get_storage())
    return _ingester


# ─────────────────────────────────────────────────────────────────────────────
# 请求/响应模型
# ─────────────────────────────────────────────────────────────────────────────

class KBCreateRequest(BaseModel):
    title: str
    category: str = "other"
    symptom: str
    root_cause: str
    remediation: str
    verify_method: Optional[str] = None
    applicable_os: List[str] = Field(default_factory=list)
    applicable_service: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    confidence: str = "medium"
    notes: Optional[str] = None
    #: 来源：manual / ai_diagnosis / …
    source: Optional[str] = None
    #: 关联审计 / 诊断 trace
    trace_id: Optional[str] = None
    #: 适用主机范围展示名（写入 notes + tags）
    host_scope: Optional[str] = None


class KBRatingRequest(BaseModel):
    rating: float = Field(..., ge=0, le=5)


class ScriptCreateRequest(BaseModel):
    name: str
    description: str
    content: str
    language: str = "bash"
    applicable_os: List[str] = Field(default_factory=list)
    risk_level: str = "medium"
    category: str = "other"
    tags: List[str] = Field(default_factory=list)
    parameters: Optional[Dict[str, Any]] = None
    parameter_examples: Optional[Dict[str, Any]] = None
    prerequisites: Optional[str] = None
    expected_duration_sec: int = 30


class ScriptUseRequest(BaseModel):
    success: bool


class BPracticeCreateRequest(BaseModel):
    title: str
    description: str
    steps: str
    applicable_scenarios: List[str] = Field(default_factory=list)
    applicable_os: List[str] = Field(default_factory=list)
    category: str = "other"
    tags: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None


class IngestFromRemediatorRequest(BaseModel):
    trace_id: str
    error_info: Dict[str, Any]
    remediation_steps: List[Dict[str, Any]]
    success: bool = True
    environment_id: Optional[str] = None


class IngestFromTerminalRequest(BaseModel):
    command: str
    nl_intent: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    os_hint: Optional[str] = None


class PendingApproveRequest(BaseModel):
    """批准闭环候选入库时可覆盖标题与分类。"""
    title: Optional[str] = None
    category: Optional[str] = None
    extra_tags: List[str] = Field(default_factory=list)
    reviewed_by: str = "operator"


class PendingRejectRequest(BaseModel):
    reason: Optional[str] = None
    reviewed_by: str = "operator"


# ─────────────────────────────────────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────────────────────────────────────

# ── 统计 ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats():
    """知识库统计：KB 条目数 / 脚本数 / 最佳实践数。"""
    return _get_storage().get_stats()


# ── 检索 ───────────────────────────────────────────────────────────────────

@router.get("/search")
async def search(
    q: str = Query(..., description="查询文本"),
    mode: str = Query("all", description="kb | script | best_practice | all"),
    category: Optional[str] = Query(None, description="分类过滤"),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    """全文检索（支持 KB / 脚本 / 最佳实践）。"""
    try:
        sq = SearchQuery(
            q=q,
            mode=mode,  # type: ignore
            category=KBCategory(category) if category else None,
            limit=limit,
            offset=offset,
        )
    except ValueError:
        raise HTTPException(400, f"无效的 category: {category}")

    return _get_search().search(sq).model_dump()


@router.get("/suggest")
async def suggest(
    q: str = Query(..., description="查询文本"),
    mode: str = Query("kb", description="kb | script"),
):
    """轻量推荐：用于 Agent 执行前预热（限制5条）。"""
    sq = SearchQuery(q=q, mode=mode, limit=5)  # type: ignore
    resp = _get_search().search(sq)
    return [r.model_dump() for r in resp.results]


# ── KB CRUD ────────────────────────────────────────────────────────────────

@router.get("/kb")
async def list_kb(
    category: Optional[str] = None,
    source: Optional[str] = None,
    tags: Optional[str] = Query(None, description="逗号分隔的标签"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出 KB 条目。"""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    entries = _get_storage().list_kb_entries(
        category=category,
        source=source,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
    total = _get_storage().count_kb_entries(category=category)
    return {"total": total, "entries": [e.model_dump() for e in entries]}


@router.get("/kb/{entry_id}")
async def get_kb(entry_id: str):
    """获取单条 KB 条目。"""
    entry = _get_storage().get_kb_entry(entry_id)
    if not entry:
        raise HTTPException(404, "KB 条目不存在")
    return entry.model_dump()


@router.post("/kb")
async def create_kb(body: KBCreateRequest):
    """手动录入 / AI 诊断入库 KB 条目。"""
    try:
        src_raw = (body.source or "").strip().lower()
        if src_raw in ("ai_diagnosis", IngestSource.AI_DIAGNOSIS.value):
            source = IngestSource.AI_DIAGNOSIS.value
        elif src_raw in ("fleet_report", IngestSource.FLEET_REPORT.value):
            source = IngestSource.FLEET_REPORT.value
        elif src_raw and src_raw in {e.value for e in IngestSource}:
            source = src_raw
        else:
            source = IngestSource.MANUAL.value
        scope = (body.host_scope or "").strip()[:80]
        notes = (body.notes or "").strip()
        if scope:
            prefix = f"适用范围: {scope}"
            notes = f"{prefix}\n{notes}".strip() if notes else prefix
        tags = list(body.tags or [])
        if source == IngestSource.AI_DIAGNOSIS.value and "ai_diagnosis" not in tags:
            tags = ["ai_diagnosis"] + tags
        if source == IngestSource.FLEET_REPORT.value and "fleet_report" not in tags:
            tags = ["fleet_report"] + tags
        if scope and f"scope:{scope}" not in tags:
            tags = tags + [f"scope:{scope}"]
        entry = KBEntry(
            title=body.title,
            category=KBCategory(body.category),
            symptom=body.symptom,
            root_cause=body.root_cause,
            remediation=body.remediation,
            verify_method=body.verify_method,
            applicable_os=body.applicable_os,
            applicable_service=body.applicable_service,
            tags=tags[:24],
            confidence=KBConfidence(body.confidence),
            source=source,
            source_id=(body.trace_id or "").strip()[:64] or None,
            notes=notes or None,
        )
    except ValueError as e:
        raise HTTPException(400, f"参数错误: {e}")

    _get_storage().save_kb_entry(entry)
    logger.info(f"[KB API] 创建 KB 条目: {entry.id} source={entry.source}")
    try:
        from chibycore.platform_audit import append_platform_audit

        append_platform_audit(
            "knowledge_ingest",
            trace_id=(body.trace_id or "").strip(),
            result_summary=f"KB {entry.id}: {(entry.title or '')[:80]}",
            outcome="success",
            host_scope={"display_name": (body.host_scope or "").strip()} if body.host_scope else None,
            metadata={
                "entry_id": entry.id,
                "source": entry.source,
                "title": entry.title,
            },
            mirror_mobile=True,
        )
    except Exception:
        logger.debug("knowledge_ingest audit skipped", exc_info=True)
    return {"id": entry.id, "ok": True, "source": entry.source}


@router.patch("/kb/{entry_id}")
async def update_kb(entry_id: str, body: KBCreateRequest):
    """更新 KB 条目。"""
    existing = _get_storage().get_kb_entry(entry_id)
    if not existing:
        raise HTTPException(404, "KB 条目不存在")
    try:
        for k, v in {
            "title": body.title,
            "category": KBCategory(body.category),
            "symptom": body.symptom,
            "root_cause": body.root_cause,
            "remediation": body.remediation,
            "verify_method": body.verify_method,
            "applicable_os": body.applicable_os,
            "applicable_service": body.applicable_service,
            "tags": body.tags,
            "confidence": KBConfidence(body.confidence),
            "notes": body.notes,
        }.items():
            setattr(existing, k, v)
        _get_storage().save_kb_entry(existing)
    except ValueError as e:
        raise HTTPException(400, f"参数错误: {e}")
    return {"id": entry_id, "ok": True}


@router.delete("/kb/{entry_id}")
async def delete_kb(entry_id: str):
    """删除 KB 条目。"""
    ok = _get_storage().delete_kb_entry(entry_id)
    if not ok:
        raise HTTPException(404, "KB 条目不存在")
    return {"ok": True}


@router.post("/kb/{entry_id}/rate")
async def rate_kb(entry_id: str, body: KBRatingRequest):
    """对 KB 条目评分。"""
    entry = _get_storage().get_kb_entry(entry_id)
    if not entry:
        raise HTTPException(404, "KB 条目不存在")
    entry.update_rating(body.rating)
    _get_storage().save_kb_entry(entry)
    return {"id": entry_id, "rating": entry.rating, "rating_count": entry.rating_count}


@router.post("/kb/{entry_id}/feedback")
async def feedback_kb(entry_id: str, success: bool = Query(...)):
    """反馈 KB 条目应用结果（成功/失败）。"""
    entry = _get_storage().get_kb_entry(entry_id)
    if not entry:
        raise HTTPException(404, "KB 条目不存在")
    if success:
        entry.record_success()
    else:
        entry.record_failure()
    _get_storage().save_kb_entry(entry)
    return {"id": entry_id, "success_count": entry.success_count, "failure_count": entry.failure_count}


# ── 脚本库 CRUD ────────────────────────────────────────────────────────────

@router.get("/scripts")
async def list_scripts(
    category: Optional[str] = None,
    language: Optional[str] = None,
    risk_level: Optional[str] = None,
    tags: Optional[str] = Query(None, description="逗号分隔的标签"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出脚本。"""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    entries = _get_storage().list_script_entries(
        category=category,
        language=language,
        risk_level=risk_level,
        tags=tag_list,
        limit=limit,
        offset=offset,
    )
    total = _get_storage().count_script_entries(category=category)
    return {"total": total, "entries": [e.model_dump() for e in entries]}


@router.get("/scripts/{script_id}")
async def get_script(script_id: str):
    """获取脚本详情（包含 content）。"""
    entry = _get_storage().get_script_entry(script_id)
    if not entry:
        raise HTTPException(404, "脚本不存在")
    return entry.model_dump()


@router.post("/scripts")
async def create_script(body: ScriptCreateRequest):
    """注册新脚本。"""
    try:
        entry = ScriptEntry(
            name=body.name,
            description=body.description,
            content=body.content,
            language=ScriptLanguage(body.language),
            risk_level=ScriptRiskLevel(body.risk_level),
            category=KBCategory(body.category),
            tags=body.tags,
            parameters=body.parameters,
            parameter_examples=body.parameter_examples,
            prerequisites=body.prerequisites,
            applicable_os=body.applicable_os,
            expected_duration_sec=body.expected_duration_sec,
        )
    except ValueError as e:
        raise HTTPException(400, f"参数错误: {e}")

    _get_storage().save_script_entry(entry)
    logger.info(f"[KB API] 注册脚本: {entry.id}")
    return {"id": entry.id, "ok": True}


@router.post("/scripts/{script_id}/use")
async def use_script(script_id: str, body: ScriptUseRequest):
    """记录脚本使用结果。"""
    entry = _get_storage().get_script_entry(script_id)
    if not entry:
        raise HTTPException(404, "脚本不存在")
    entry.record_use(body.success)
    _get_storage().save_script_entry(entry)
    return {"id": script_id, "use_count": entry.use_count, "success_count": entry.success_count}


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: str):
    """删除脚本。"""
    ok = _get_storage().delete_script_entry(script_id)
    if not ok:
        raise HTTPException(404, "脚本不存在")
    return {"ok": True}


# ── 最佳实践 CRUD ──────────────────────────────────────────────────────────

@router.get("/best-practices")
async def list_bp(
    category: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出最佳实践。"""
    entries = _get_storage().list_best_practices(category=category, limit=limit, offset=offset)
    return {"entries": [e.model_dump() for e in entries]}


@router.post("/best-practices")
async def create_bp(body: BPracticeCreateRequest):
    """录入最佳实践。"""
    try:
        entry = BestPractice(
            title=body.title,
            description=body.description,
            steps=body.steps,
            applicable_scenarios=body.applicable_scenarios,
            applicable_os=body.applicable_os,
            category=KBCategory(body.category),
            tags=body.tags,
            source_url=body.source_url,
        )
    except ValueError as e:
        raise HTTPException(400, f"参数错误: {e}")

    _get_storage().save_best_practice(entry)
    return {"id": entry.id, "ok": True}


# ── 知识沉淀 ───────────────────────────────────────────────────────────────

@router.post("/ingest/remediator")
async def ingest_from_remediator(body: IngestFromRemediatorRequest):
    """从 remediator 成功案例自动沉淀为 KB 条目。"""
    entry = _get_ingester().ingest_from_remediator(
        trace_id=body.trace_id,
        error_info=body.error_info,
        remediation_steps=body.remediation_steps,
        success=body.success,
        environment_id=body.environment_id,
    )
    if not entry:
        return {"ok": False, "message": "未沉淀（失败或无有效数据）"}
    return {"ok": True, "id": entry.id, "title": entry.title}


@router.post("/ingest/terminal")
async def ingest_from_terminal(body: IngestFromTerminalRequest):
    """从 terminal 成功会话自动沉淀为 KB 条目。"""
    entry = _get_ingester().ingest_from_terminal_session(
        command=body.command,
        nl_intent=body.nl_intent,
        stdout=body.stdout,
        stderr=body.stderr,
        exit_code=body.exit_code,
        os_hint=body.os_hint,
    )
    if not entry:
        return {"ok": False, "message": "未沉淀（只读命令或已有重复）"}
    return {"ok": True, "id": entry.id, "title": entry.title}


# ── 闭环候选队列（人工批准后入库）───────────────────────────────────────────


@router.get("/pending")
async def list_kb_pending(
    status: str = Query(
        "pending",
        description="pending | approved | rejected | all",
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """列出闭环生成的 KB 候选（默认仅 pending）。"""
    st = _get_storage()
    filt: Optional[str] = None if status == "all" else status
    total = st.count_pending_candidates(status=filt)
    rows = st.list_pending_candidates(status=filt, limit=limit, offset=offset)
    return {
        "total": total,
        "candidates": [r.model_dump(mode="json") for r in rows],
    }


@router.get("/pending/{candidate_id}")
async def get_kb_pending(candidate_id: str):
    cand = _get_storage().get_pending_candidate(candidate_id)
    if not cand:
        raise HTTPException(404, "候选不存在")
    return cand.model_dump(mode="json")


@router.post("/pending/{candidate_id}/approve")
async def approve_kb_pending(candidate_id: str, body: PendingApproveRequest):
    from chibycore.knowledge_hub.closure_candidate import pending_candidate_to_kb_entry

    st = _get_storage()
    cand = st.get_pending_candidate(candidate_id)
    if not cand:
        raise HTTPException(404, "候选不存在")
    if cand.status != PendingKBStatus.PENDING:
        raise HTTPException(400, "仅 pending 状态可批准入库")

    cat = None
    if body.category:
        try:
            cat = KBCategory(body.category)
        except ValueError as e:
            raise HTTPException(400, f"无效 category: {e}") from e

    entry = pending_candidate_to_kb_entry(cand, category=cat, title_override=body.title)
    if body.extra_tags:
        entry.tags = list({*entry.tags, *body.extra_tags})
    st.save_kb_entry(entry)
    st.update_pending_candidate_review(
        candidate_id,
        status=PendingKBStatus.APPROVED,
        reviewed_by=body.reviewed_by,
    )
    logger.info("[KB API] 候选已批准入库 candidate=%s kb_id=%s", candidate_id, entry.id)
    return {"ok": True, "kb_entry_id": entry.id, "title": entry.title}


@router.post("/pending/{candidate_id}/reject")
async def reject_kb_pending(candidate_id: str, body: PendingRejectRequest):
    st = _get_storage()
    cand = st.get_pending_candidate(candidate_id)
    if not cand:
        raise HTTPException(404, "候选不存在")
    if cand.status != PendingKBStatus.PENDING:
        raise HTTPException(400, "仅 pending 状态可拒绝")
    st.update_pending_candidate_review(
        candidate_id,
        status=PendingKBStatus.REJECTED,
        reviewed_by=body.reviewed_by,
        reject_reason=body.reason,
    )
    return {"ok": True}


# ── 导出 ───────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_all():
    """导出全部数据（JSON）。"""
    return _get_storage().export_all()
