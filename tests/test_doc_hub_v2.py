"""DocHub v2：语义切片、混合检索、质量门禁、统一知识调度。"""
from __future__ import annotations

from pathlib import Path

import pytest

from chibycore.doc_hub.chunker_v2 import chunk_plain_text, chunk_parsed_document
from chibycore.doc_hub.chunker_v2 import ParsedDocument, Section
from chibycore.doc_hub.embeddings import HashEmbedder
from chibycore.doc_hub.ingest import DocHubIngester
from chibycore.doc_hub.search import DocHubSearch, rrf_fuse
from chibycore.doc_hub.storage import DocHubStorage
from chibycore.doc_hub.structured_parse import parse_markdown_sections
from chibycore.doc_hub.vector_store import InMemoryVectorStore
from chibycore.knowledge_orchestrator import get_content, search_knowledge
from terminal.mobile.doc_tools import run_doc_search
from terminal.mobile.orchestrator_tools import ORCH_TOOLS
from terminal.mobile.remote_tools import (
    DEFAULT_ALLOWED_TOOLS,
    RemoteToolCall,
    call_needs_confirmation,
    execute_remote_tool_call,
    parse_remote_tool_calls,
)


@pytest.fixture()
def doc_env(tmp_path: Path):
    DocHubStorage.reset_instance()
    store = DocHubStorage(root_dir=tmp_path / "doc_hub")
    vectors = InMemoryVectorStore()
    embedder = HashEmbedder(dim=64)
    ingester = DocHubIngester(storage=store, vector_store=vectors, embedder=embedder)
    search = DocHubSearch(storage=store, vector_store=vectors, embedder=embedder)
    yield store, ingester, search
    DocHubStorage.reset_instance()


def test_chunker_v2_title_chain():
    doc = parse_markdown_sections(
        "# 运维手册\n\n前言内容足够长。" + ("说明。" * 30) + "\n\n"
        "## 数据库\n\n重启步骤一。重启步骤二。重启步骤三。" + ("细节。" * 40) + "\n",
        doc_title="运维手册",
    )
    chunks = chunk_parsed_document(doc, target_min=100, target_max=400)
    assert chunks
    assert any("数据库" in (c.title_chain or "") or "数据库" in c.text for c in chunks)


def test_rrf_fuse_order():
    fused = rrf_fuse([["a", "b", "c"], ["b", "a", "d"]], k=30)
    ids = [x[0] for x in fused]
    assert ids[0] in ("a", "b")


def test_search_debug_payload(doc_env):
    store, ingester, search = doc_env
    f = store.root_dir / "dbg.md"
    f.write_text(
        "# 调试手册\n\n关键词命中专用术语：蓝色变更窗口。"
        + ("补充段落。" * 25),
        encoding="utf-8",
    )
    assert ingester.ingest_file(f, async_if_large=False)["ok"]
    resp = search.search("蓝色变更窗口", limit=3, strategy="hybrid", debug=True, rrf_k=30)
    assert resp.debug is not None
    assert resp.debug.get("rrf_k") == 30
    assert "vector" in resp.debug and "keyword" in resp.debug and "fused" in resp.debug
    assert resp.debug.get("fetch_n") >= 3


def test_chunk_corpus_stats(doc_env):
    store, ingester, _ = doc_env
    f = store.root_dir / "stats.md"
    f.write_text("# T\n\n" + ("内容足够长用于入库统计。" * 20), encoding="utf-8")
    assert ingester.ingest_file(f, async_if_large=False)["ok"]
    st = store.chunk_corpus_stats()
    assert st["chunk_count"] >= 1
    assert st["chunk_avg_len"] > 0
    assert st["fts_row_count"] >= 1


def test_pdf_structure_from_spans():
    from chibycore.doc_hub.pdf_structure import _Span, build_sections_from_spans

    spans = [
        _Span(text="运维手册", size=18.0, page=0),
        _Span(text="第一章 总则", size=14.0, page=0),
        _Span(text="本章说明系统维护原则与值班要求。" * 3, size=10.0, page=0),
        _Span(text="第二章 变更", size=14.0, page=0),
        _Span(text="变更必须走审批流程，禁止擅自操作。" * 3, size=10.0, page=0),
    ]
    sections, quality = build_sections_from_spans(spans, doc_title="运维手册")
    assert quality in ("high", "medium")
    titles = [s.title for s in sections]
    assert any("运维" in t or "总则" in t or "变更" in t for t in titles) or any(
        c.title for s in sections for c in s.children
    )


def test_reindex_progress_summary():
    from chibycore.doc_hub.reindex_job import ReindexJobManager

    mgr = ReindexJobManager()
    assert mgr.progress_summary()["reindex_in_progress"] is False
    with mgr._lock:
        mgr._jobs["abc"] = {
            "status": "running",
            "total": 10,
            "done": 3,
            "ok": 2,
            "failed": 1,
            "errors": [],
        }
    sm = mgr.progress_summary()
    assert sm["reindex_in_progress"] is True
    assert sm["reindex_job_id"] == "abc"
    assert sm["reindex_done"] == 3


def test_text_too_short_gate(doc_env):
    store, ingester, _search = doc_env
    f = store.root_dir / "short.txt"
    f.write_text("太短了", encoding="utf-8")
    r = ingester.ingest_file(f, async_if_large=False)
    assert r["ok"] is False
    assert r.get("error_code") == "text_too_short"


def test_ingest_hybrid_search(doc_env):
    store, ingester, search = doc_env
    f = store.root_dir / "sample.md"
    body = (
        "# 变更窗口审批\n\n生产变更必须在变更窗口内提交审批单，"
        "经值班经理签字后方可执行。紧急变更走快速通道。\n\n"
        + ("其他说明段落用于满足长度门禁。" * 8)
    )
    f.write_text(body, encoding="utf-8")
    result = ingester.ingest_file(f, copy_into_store=True, async_if_large=False)
    assert result["ok"] is True
    assert result["chunk_count"] >= 1

    resp = search.search("变更窗口审批", limit=5, strategy="hybrid")
    assert resp.total >= 1
    assert resp.strategy == "hybrid"

    kw = search.search("变更窗口", limit=5, strategy="keyword")
    assert kw.total >= 1


def test_doc_tools_and_orch_whitelist(doc_env):
    store, ingester, search = doc_env
    f = store.root_dir / "ops.txt"
    f.write_text(
        "nginx 反向代理超时建议调大 proxy_read_timeout 到 120s。"
        + ("详细说明与注意事项。" * 20),
        encoding="utf-8",
    )
    r = ingester.ingest_file(f, async_if_large=False)
    assert r["ok"]

    data = run_doc_search(q="nginx 超时", limit=3, storage=store, search=search)
    assert data["ok"] is True
    assert data["total"] >= 1

    assert "search_knowledge" in DEFAULT_ALLOWED_TOOLS
    assert ORCH_TOOLS == frozenset({"search_knowledge", "get_content"})


@pytest.mark.asyncio
async def test_search_knowledge_tool_execute(doc_env):
    store, ingester, search = doc_env
    f = store.root_dir / "kbdoc.md"
    f.write_text("# 手册\n\n" + ("内容关于磁盘告警处理流程。" * 15), encoding="utf-8")
    assert ingester.ingest_file(f, async_if_large=False)["ok"]

    # 仅 doc 源（避免依赖空 KH）
    from chibycore.knowledge_orchestrator import search_knowledge as sk

    out = sk("磁盘告警", sources=["doc"], limit=3)
    assert out["ok"] is True

    call = RemoteToolCall(
        tool="search_knowledge",
        raw={"tool": "search_knowledge", "q": "磁盘", "sources": ["doc"]},
    )

    async def _boom(*_a, **_k):
        raise AssertionError("no ssh")

    # monkeypatch run path uses real orch; execute_remote_tool_call
    tr = await execute_remote_tool_call(
        call,
        executor=_boom,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
    )
    assert tr.ok is True
    assert call_needs_confirmation(call) is False

    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"search_knowledge","q":"磁盘","sources":["doc"]}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text, allowed_tools=list(DEFAULT_ALLOWED_TOOLS))
    assert any(c.tool == "search_knowledge" for c in calls)
