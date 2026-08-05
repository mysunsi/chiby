"""本地知识库工具 kb_search / kb_get / kb_ingest。"""

from __future__ import annotations

from pathlib import Path

import pytest

from chibycore.knowledge_hub.models import KBCategory, KBConfidence, KBEntry
from chibycore.knowledge_hub.storage import KnowledgeHubStorage
from terminal.mobile.kb_tools import (
    KB_READONLY_TOOLS,
    KB_TOOLS,
    format_kb_result_summary,
    kb_ingest_allowed,
    run_kb_get,
    run_kb_ingest,
    run_kb_search,
)
from terminal.mobile.remote_tools import (
    DEFAULT_ALLOWED_TOOLS,
    RemoteToolCall,
    call_needs_confirmation,
    execute_remote_tool_call,
    format_tool_results_for_user,
    parse_remote_tool_calls,
)


@pytest.fixture()
def kb_store(tmp_path: Path) -> KnowledgeHubStorage:
    db = tmp_path / "kb_test.db"
    store = KnowledgeHubStorage(str(db))
    store.save_kb_entry(
        KBEntry(
            id="nginx502abcd",
            title="nginx 连接数过高导致 502",
            category=KBCategory.SERVICE_OPS,
            symptom="上游返回 502 Bad Gateway，worker 连接打满",
            root_cause="worker_connections 过低或 upstream 超时",
            remediation="调大 worker_connections；检查 upstream；nginx -t && reload",
            verify_method="curl -I 返回非 502；error.log 无 upstream timed out",
            tags=["nginx", "502"],
            confidence=KBConfidence.HIGH,
            source="manual",
            applicable_os=["linux"],
            applicable_service="nginx",
        )
    )
    return store


def test_kb_tools_in_default_allowlist():
    assert "kb_search" in DEFAULT_ALLOWED_TOOLS
    assert "kb_get" in DEFAULT_ALLOWED_TOOLS
    assert "kb_ingest" in DEFAULT_ALLOWED_TOOLS
    assert KB_TOOLS <= set(DEFAULT_ALLOWED_TOOLS)
    assert KB_READONLY_TOOLS == frozenset({"kb_search", "kb_get"})


def test_parse_kb_remote_tool():
    text = (
        "查一下手册\n"
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"kb_search","q":"nginx 502","mode":"kb","limit":5}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text, allowed_tools=list(DEFAULT_ALLOWED_TOOLS))
    assert len(calls) == 1
    assert calls[0].tool == "kb_search"
    assert calls[0].raw.get("q") == "nginx 502"


def test_kb_search_hit_and_empty(kb_store: KnowledgeHubStorage):
    hit = run_kb_search(q="nginx 502", mode="kb", limit=5, storage=kb_store)
    assert hit["ok"] is True
    assert hit["total"] >= 1
    assert any("502" in (r.get("title") or "") for r in hit["results"])
    assert "nginx502abcd" in {r.get("entry_id") for r in hit["results"]}

    empty_store = KnowledgeHubStorage(str(Path(kb_store.db_path).parent / "empty.db"))
    miss = run_kb_search(q="nginx 502", mode="kb", limit=5, storage=empty_store)
    assert miss["ok"] is True
    assert miss["total"] == 0
    assert miss["results"] == []

    bad = run_kb_search(q="", storage=kb_store)
    assert bad["ok"] is False
    assert bad["error_code"] == "query_required"


def test_kb_get_and_not_found(kb_store: KnowledgeHubStorage):
    got = run_kb_get(entry_id="nginx502abcd", storage=kb_store)
    assert got["ok"] is True
    assert "worker_connections" in (got.get("remediation") or "")
    assert got["entry_type"] == "kb"

    miss = run_kb_get(entry_id="missing00001", storage=kb_store)
    assert miss["ok"] is False
    assert miss["error_code"] == "not_found"


def test_kb_ingest_mode_gate(kb_store: KnowledgeHubStorage):
    assert kb_ingest_allowed("omnipotent") is True
    assert kb_ingest_allowed("intelligent") is True
    assert kb_ingest_allowed("efficient") is False
    assert kb_ingest_allowed("advanced") is True  # legacy → intelligent

    denied = run_kb_ingest(
        title="t",
        symptom="s",
        agent_mode="efficient",
        storage=kb_store,
    )
    assert denied["ok"] is False
    assert denied["error_code"] == "mode_denied"

    ok = run_kb_ingest(
        title="磁盘 inode 耗尽",
        symptom="df -i 显示 100%",
        root_cause="小文件过多",
        remediation="清理临时文件；扩容 inode",
        agent_mode="omnipotent",
        storage=kb_store,
    )
    assert ok["ok"] is True
    eid = ok["entry_id"]
    got = run_kb_get(entry_id=eid, storage=kb_store)
    assert got["ok"] is True
    assert "inode" in (got.get("title") or "").lower() or "inode" in (
        got.get("symptom") or ""
    )


def test_call_needs_confirmation_kb():
    assert call_needs_confirmation(RemoteToolCall(tool="kb_search")) is False
    assert call_needs_confirmation(RemoteToolCall(tool="kb_get")) is False
    assert call_needs_confirmation(RemoteToolCall(tool="kb_ingest")) is True


def test_format_user_and_memory_not_confused(kb_store: KnowledgeHubStorage):
    """kb 结果展示走本地工具条；与主机 memory 意图无关。"""
    data = run_kb_search(q="nginx", mode="kb", storage=kb_store)
    summary = format_kb_result_summary(data)
    assert "nginx" in summary.lower() or "502" in summary
    assert "MEMORY.md" not in summary

    from terminal.mobile.remote_tools import RemoteToolResult

    tr = RemoteToolResult(tool="kb_search", ok=True, data=data, stdout=summary)
    text = format_tool_results_for_user([tr])
    assert "`$ kb_search`" in text
    assert "<<<EXEC_BODY>>>" in text


@pytest.mark.asyncio
async def test_execute_kb_search_tool(kb_store: KnowledgeHubStorage, monkeypatch):
    """execute_remote_tool_call 短路执行 kb_search（不经 SSH）。"""
    import terminal.mobile.kb_tools as kb_mod

    real_search = kb_mod.run_kb_search

    def _fake_search(**kwargs):
        kwargs = dict(kwargs)
        kwargs["storage"] = kb_store
        return real_search(**kwargs)

    monkeypatch.setattr(kb_mod, "run_kb_search", _fake_search)
    call = RemoteToolCall(
        tool="kb_search",
        raw={"tool": "kb_search", "q": "nginx 502", "mode": "kb", "limit": 3},
    )

    async def _never(*_a, **_k):
        raise AssertionError("SSH executor must not run for kb_search")

    class _Exec:
        run = staticmethod(_never)

    tr = await execute_remote_tool_call(
        call,
        executor=_Exec(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: None,
        agent_mode="omnipotent",
    )
    assert tr.ok is True
    assert tr.tool == "kb_search"
    assert (tr.data or {}).get("total", 0) >= 1


def test_hermes_protocol_mentions_kb_not_memory_for_host_ram():
    from terminal.mobile.hermes_protocol import advanced_protocol_preamble

    pre = advanced_protocol_preamble("hid1", "ssh", solo_remote_tools=False)
    assert "kb_search" in pre
    assert "KnowledgeHub" in pre or "知识库" in pre
    assert "本会话已禁用 memory 工具" in pre
    assert "目标主机" in pre
    # 主机内存问法仍锚定系统资源，而非 KB/Memory
    assert "绝不是" in pre and "memory 工具" in pre


def test_kb_ingest_pending_roundtrip_keeps_title_symptom():
    """确认卡往返必须保留 title/symptom，否则批准后 fields_required。"""
    from terminal.mobile.kb_tools import extract_kb_args
    from terminal.mobile.remote_tools import (
        remote_tool_call_from_pending_dict,
        remote_tool_call_to_pending_dict,
    )

    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"kb_ingest","title":"内存瓶颈排查","symptom":"可用内存仅 782MB",'
        '"root_cause":"CloudBeaver Xmx 过大","remediation":"降 Xmx 并加 Swap",'
        '"category":"performance"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text, allowed_tools=list(DEFAULT_ALLOWED_TOOLS))
    assert len(calls) == 1
    assert calls[0].tool == "kb_ingest"
    pending = remote_tool_call_to_pending_dict(calls[0])
    assert pending.get("title") == "内存瓶颈排查"
    assert pending.get("symptom") == "可用内存仅 782MB"
    assert pending.get("root_cause")
    assert pending.get("host") == ""
    assert "内存瓶颈" in str(pending.get("preview") or "")

    restored = remote_tool_call_from_pending_dict(pending)
    assert restored is not None
    args = extract_kb_args(restored.raw or {})
    assert args["title"] == "内存瓶颈排查"
    assert args["symptom"] == "可用内存仅 782MB"
    assert "CloudBeaver" in args["root_cause"]


@pytest.mark.asyncio
async def test_execute_kb_ingest_after_pending_roundtrip(
    kb_store: KnowledgeHubStorage, monkeypatch
):
    import terminal.mobile.kb_tools as kb_mod
    from terminal.mobile.remote_tools import (
        remote_tool_call_from_pending_dict,
        remote_tool_call_to_pending_dict,
    )

    real_ingest = kb_mod.run_kb_ingest

    def _fake_ingest(**kwargs):
        kwargs = dict(kwargs)
        kwargs["storage"] = kb_store
        return real_ingest(**kwargs)

    monkeypatch.setattr(kb_mod, "run_kb_ingest", _fake_ingest)
    call = RemoteToolCall(
        tool="kb_ingest",
        raw={
            "tool": "kb_ingest",
            "title": "Ubuntu 内存慢",
            "symptom": "free -h 可用不足 1GB",
            "root_cause": "Java 堆过大",
            "remediation": "下调 -Xmx",
        },
    )
    pending = remote_tool_call_to_pending_dict(call)
    restored = remote_tool_call_from_pending_dict(pending)
    assert restored is not None

    async def _never(*_a, **_k):
        raise AssertionError("SSH must not run for kb_ingest")

    class _Exec:
        run = staticmethod(_never)

    tr = await execute_remote_tool_call(
        restored,
        executor=_Exec(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: None,
        agent_mode="omnipotent",
    )
    assert tr.ok is True, (tr.error_code, tr.error)
    assert (tr.data or {}).get("entry_id")
    assert (tr.data or {}).get("title") == "Ubuntu 内存慢"