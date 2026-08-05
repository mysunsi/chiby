"""DocHub：切片、入库、检索、Agent 工具。"""
from __future__ import annotations

from pathlib import Path

import pytest

from chibycore.doc_hub.chunker import chunk_text
from chibycore.doc_hub.embeddings import HashEmbedder
from chibycore.doc_hub.ingest import DocHubIngester
from chibycore.doc_hub.search import DocHubSearch
from chibycore.doc_hub.storage import DocHubStorage
from chibycore.doc_hub.vector_store import InMemoryVectorStore
from terminal.mobile.doc_tools import (
    DOC_READONLY_TOOLS,
    format_doc_result_summary,
    run_doc_get,
    run_doc_search,
)
from terminal.mobile.remote_tools import (
    DEFAULT_ALLOWED_TOOLS,
    call_needs_confirmation,
    parse_remote_tool_calls,
    RemoteToolCall,
    execute_remote_tool_call,
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


def test_chunk_text_overlap():
    text = "A" * 2500
    chunks = chunk_text(text, chunk_size=1000, overlap_ratio=0.15)
    assert len(chunks) >= 2
    assert chunks[0][0] == 0
    assert all(c[1] for c in chunks)


@pytest.mark.asyncio
async def test_upload_multiple_files_via_api(doc_env, monkeypatch, tmp_path):
    """多文件必须走 form.getlist，不能依赖 File(None) 列表注入。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    store, ingester, _search = doc_env
    import chibycore.doc_hub.api as api_mod

    monkeypatch.setattr(api_mod, "_get_ingester", lambda: ingester)
    monkeypatch.setattr(api_mod, "_get_storage", lambda: store)
    monkeypatch.setattr(api_mod, "_get_search", lambda: _search)

    app = FastAPI()
    app.include_router(api_mod.router, prefix="/api/docs")

    body1 = ("变更窗口审批说明。" + ("补充。" * 40)).encode("utf-8")
    body2 = ("nginx 超时调优手册。" + ("细节。" * 40)).encode("utf-8")
    files = [
        ("files", ("a.md", body1, "text/markdown")),
        ("files", ("b.md", body2, "text/markdown")),
    ]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/docs/upload", files=files)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data.get("total") == 2
    assert data.get("imported") == 2
    assert store.count_documents() >= 2


def test_ingest_and_search_topk(doc_env):
    store, ingester, search = doc_env
    f = store.root_dir / "sample.md"
    f.write_text(
        "# 变更窗口审批\n\n生产变更必须在变更窗口内提交审批单，"
        "经值班经理签字后方可执行。紧急变更走快速通道。\n\n"
        + ("其他说明。\n" * 40),
        encoding="utf-8",
    )
    result = ingester.ingest_file(f, copy_into_store=True, async_if_large=False)
    assert result["ok"] is True
    assert result["status"] == "ready"
    assert result["chunk_count"] >= 1

    resp = search.search("变更窗口审批流程", limit=5)
    assert resp.total >= 1
    assert resp.results[0].doc_id == result["doc_id"]
    assert "变更" in resp.results[0].snippet or "审批" in resp.results[0].title


def test_doc_tools_search_get(doc_env):
    store, ingester, search = doc_env
    f = store.files_dir.parent / "ops.txt"
    f.write_text(
        "nginx 反向代理超时建议调大 proxy_read_timeout 到 120s。"
        + ("补充说明用于满足入库长度门禁。" * 8),
        encoding="utf-8",
    )
    r = ingester.ingest_file(f, async_if_large=False)
    assert r["ok"]

    data = run_doc_search(q="nginx 超时", limit=3, storage=store, search=search)
    assert data["ok"] is True
    assert data["total"] >= 1
    hit = data["hits"][0]
    got = run_doc_get(chunk_id=hit["chunk_id"], storage=store)
    assert got["ok"] is True
    assert "proxy_read_timeout" in got["text"]
    summary = format_doc_result_summary(data)
    assert "命中" in summary


def test_doc_tools_in_whitelist_and_no_confirm():
    assert "doc_search" in DEFAULT_ALLOWED_TOOLS
    assert "doc_get" in DEFAULT_ALLOWED_TOOLS
    assert DOC_READONLY_TOOLS == frozenset({"doc_search", "doc_get"})
    assert call_needs_confirmation(RemoteToolCall(tool="doc_search")) is False
    assert call_needs_confirmation(RemoteToolCall(tool="doc_get")) is False

    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"doc_search","q":"变更窗口","limit":5}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text, allowed_tools=list(DEFAULT_ALLOWED_TOOLS))
    assert len(calls) == 1
    assert calls[0].tool == "doc_search"


@pytest.mark.asyncio
async def test_execute_doc_search_tool(doc_env, monkeypatch):
    store, ingester, search = doc_env
    f = store.root_dir / "a.md"
    f.write_text("企业文档向量检索验收用例。" + ("补充内容。" * 30), encoding="utf-8")
    assert ingester.ingest_file(f, async_if_large=False)["ok"]

    def _patched_search(*, q, limit=8, storage=None, searcher=None, search=None, **_kw):
        return run_doc_search(q=q, limit=limit, storage=store, search=search)

    monkeypatch.setattr(
        "terminal.mobile.doc_tools.run_doc_search",
        _patched_search,
    )

    call = RemoteToolCall(
        tool="doc_search",
        raw={"tool": "doc_search", "q": "向量检索", "limit": 3},
    )

    async def _no_ssh(*_a, **_k):
        raise AssertionError("SSH must not run for doc_search")

    tr = await execute_remote_tool_call(
        call,
        executor=_no_ssh,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
    )
    assert tr.tool == "doc_search"
    assert tr.error_code != "host_required"
    assert tr.ok is True
    assert tr.data.get("total", 0) >= 1



def test_delete_document(doc_env):
    store, ingester, search = doc_env
    f = store.root_dir / "del.md"
    f.write_text("临时文档待删除。" + ("补充内容满足长度。" * 20), encoding="utf-8")
    r = ingester.ingest_file(f, async_if_large=False)
    assert r["ok"]
    assert ingester.delete_document(r["doc_id"]) is True
    assert store.get_document(r["doc_id"]) is None
    assert search.search("临时文档", limit=3).total == 0
