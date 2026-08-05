"""集成测试：真实运维场景串插件全链路。

不连真实 SSH/WinRM：主机面用 FakeExecutor；知识面用临时 KB/DocHub。
覆盖：parse → confirm/pending → execute_plugin/delegate → 内核 → 结果串联。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import pytest

from chibycore.doc_hub.embeddings import HashEmbedder
from chibycore.doc_hub.ingest import DocHubIngester
from chibycore.doc_hub.search import DocHubSearch
from chibycore.doc_hub.storage import DocHubStorage
from chibycore.doc_hub.vector_store import InMemoryVectorStore
from chibycore.knowledge_hub.models import KBCategory, KBConfidence, KBEntry
from chibycore.knowledge_hub.storage import KnowledgeHubStorage
from terminal.mobile.remote_tools import (
    DEFAULT_ALLOWED_TOOLS,
    RemoteToolCall,
    call_needs_confirmation,
    execute_remote_tool_call,
    parse_remote_tool_calls,
    remote_tool_call_from_pending_dict,
    remote_tool_call_to_pending_dict,
    resolve_allowed_tools,
)
from terminal.tools_catalog import build_tools_catalog
from terminal.tools_plugin_loader import (
    discover_plugins,
    is_plugin_tool,
    plugin_host_required,
    reset_registry,
)


pytestmark = pytest.mark.integration


# ── fixtures / helpers ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_plugins():
    reset_registry()
    discover_plugins(force=True)
    yield
    reset_registry()


@pytest.fixture()
def kb_store(tmp_path: Path) -> KnowledgeHubStorage:
    store = KnowledgeHubStorage(str(tmp_path / "kb_scene.db"))
    store.save_kb_entry(
        KBEntry(
            id="nginx502scene",
            title="nginx 连接数过高导致 502",
            category=KBCategory.SERVICE_OPS,
            symptom="上游返回 502 Bad Gateway，worker 连接打满",
            root_cause="worker_connections 过低或 upstream 超时",
            remediation="调大 worker_connections；nginx -t && reload",
            verify_method="curl -I 返回非 502",
            tags=["nginx", "502"],
            confidence=KBConfidence.HIGH,
            source="manual",
            applicable_os=["linux"],
            applicable_service="nginx",
        )
    )
    return store


@pytest.fixture()
def doc_env(tmp_path: Path):
    DocHubStorage.reset_instance()
    store = DocHubStorage(root_dir=tmp_path / "doc_hub_scene")
    vectors = InMemoryVectorStore()
    embedder = HashEmbedder(dim=64)
    ingester = DocHubIngester(storage=store, vector_store=vectors, embedder=embedder)
    search = DocHubSearch(storage=store, vector_store=vectors, embedder=embedder)
    yield store, ingester, search
    DocHubStorage.reset_instance()


class _Er:
    def __init__(self, stdout: str = "ok\n", ok: bool = True, exit_code: int = 0):
        self.ok = ok
        self.exit_code = exit_code
        self.stdout_tail = stdout
        self.stderr_tail = ""
        self.error = "" if ok else "failed"
        self.duration_ms = 3


class FakeExecutor:
    """记录每次远端命令；按调用序号返回可配置 stdout。"""

    def __init__(self, replies: Optional[List[str]] = None):
        self.calls: List[Dict[str, Any]] = []
        self._replies = list(replies or [])
        self._i = 0
        self.ssh_forbidden = False

    async def run(self, host_id, command, **kwargs):
        if self.ssh_forbidden:
            raise AssertionError(f"SSH must not run: host={host_id} cmd={command!r}")
        self.calls.append({"host_id": host_id, "command": command, "kwargs": kwargs})
        if self._i < len(self._replies):
            out = self._replies[self._i]
        else:
            out = f"fake-stdout-{self._i}\n"
        self._i += 1
        return _Er(out)


def _fake_host(*, conn_type: str = "ssh"):
    return type(
        "H",
        (),
        {
            "conn_type": conn_type,
            "password": "x",
            "ssh_private_key_path": None,
        },
    )()


VISIBLE_HOSTS = [
    {"id": "web1", "hostname": "web1", "host": "10.0.0.11"},
    {"id": "db1", "hostname": "db1", "host": "10.0.0.12"},
]


async def _exec(
    call: RemoteToolCall,
    *,
    executor: Any,
    list_visible_hosts: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    agent_mode: str = "omnipotent",
):
    return await execute_remote_tool_call(
        call,
        executor=executor,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: _fake_host(),
        conn_type_for=lambda _h: "ssh",
        list_visible_hosts=list_visible_hosts or (lambda: list(VISIBLE_HOSTS)),
        agent_mode=agent_mode,
    )


def _wrap_tool_block(payload: str) -> str:
    return (
        f"<<<REMOTE_TOOL>>>\n{payload}\n<<<END_REMOTE_TOOL>>>\n"
    )


# ── Story A：配置变更闭环 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scene_a_config_change_confirm_chain():
    """host_list → read → backup → write(确认卡往返) → execute。"""
    for tid in (
        "host_list",
        "remote_read_file",
        "remote_backup",
        "remote_write_file",
    ):
        assert is_plugin_tool(tid), tid
    assert plugin_host_required("remote_write_file") is True
    assert plugin_host_required("host_list") is False

    ex = FakeExecutor(
        replies=[
            "worker_connections 1024;\n",
            "backup:/var/backups/nginx.conf.20260727\n",
            "wrote ok\n",
        ]
    )

    # 1) 列可见主机（本地短路，禁 SSH）
    boom = FakeExecutor()
    boom.ssh_forbidden = True
    tr0 = await _exec(
        RemoteToolCall(tool="host_list", raw={"tool": "host_list"}),
        executor=boom,
    )
    assert tr0.ok is True
    assert (tr0.data or {}).get("count") == 2
    assert call_needs_confirmation(
        RemoteToolCall(tool="host_list", raw={"tool": "host_list"})
    ) is False

    # 2) 读现网配置
    read_call = RemoteToolCall(
        tool="remote_read_file",
        host="web1",
        path="/etc/nginx/nginx.conf",
        max_bytes=4096,
        raw={
            "tool": "remote_read_file",
            "host": "web1",
            "path": "/etc/nginx/nginx.conf",
            "max_bytes": 4096,
        },
    )
    assert call_needs_confirmation(read_call) is False
    tr1 = await _exec(read_call, executor=ex)
    assert tr1.ok is True
    assert "worker_connections" in (tr1.stdout or "")

    # 3) 变更前备份（只读语义，免确认）
    bak_call = RemoteToolCall(
        tool="remote_backup",
        host="web1",
        path="/etc/nginx/nginx.conf",
        raw={
            "tool": "remote_backup",
            "host": "web1",
            "path": "/etc/nginx/nginx.conf",
        },
    )
    assert call_needs_confirmation(bak_call) is False
    tr2 = await _exec(bak_call, executor=ex)
    assert tr2.ok is True
    assert "backup:" in (tr2.stdout or "")

    # 4) 写配置：必须确认 → pending 往返 → 批准执行
    write_call = RemoteToolCall(
        tool="remote_write_file",
        host="web1",
        path="/etc/nginx/nginx.conf",
        content="worker_connections 4096;\n",
        raw={
            "tool": "remote_write_file",
            "host": "web1",
            "path": "/etc/nginx/nginx.conf",
            "content": "worker_connections 4096;\n",
        },
    )
    assert call_needs_confirmation(write_call) is True
    pending = remote_tool_call_to_pending_dict(write_call)
    assert pending["path"] == "/etc/nginx/nginx.conf"
    assert "4096" in str(pending.get("content") or "")
    assert pending["host"] == "web1"
    restored = remote_tool_call_from_pending_dict(pending)
    assert restored is not None
    assert restored.tool == "remote_write_file"
    assert restored.path == "/etc/nginx/nginx.conf"
    assert "4096" in (restored.content or "")

    tr3 = await _exec(restored, executor=ex)
    assert tr3.ok is True, (tr3.error, tr3.error_code)
    # 写文件内核可能附带自动备份，故 executor 调用 ≥3（read+backup+write[+auto_bak]）
    assert len(ex.calls) >= 3
    assert any(
        "wrote" in (c.get("command") or "").lower()
        or "nginx.conf" in (c.get("command") or "")
        for c in ex.calls
    )
    # 结果侧：stdout 或 data 能反映写入成功
    joined = (tr3.stdout or "") + str(tr3.data or "")
    assert tr3.ok and ("wrote" in joined.lower() or tr3.data is not None)


# ── Story B：知识检索取正文（纯本地） ───────────────────────────────────────


@pytest.mark.asyncio
async def test_scene_b_knowledge_search_then_get(kb_store, monkeypatch):
    """kb_search → kb_get；全程无 SSH。"""
    import terminal.mobile.kb_tools as kb_mod

    real_search = kb_mod.run_kb_search
    real_get = kb_mod.run_kb_get

    def _search(**kwargs):
        kwargs = dict(kwargs)
        kwargs["storage"] = kb_store
        return real_search(**kwargs)

    def _get(**kwargs):
        kwargs = dict(kwargs)
        kwargs["storage"] = kb_store
        return real_get(**kwargs)

    monkeypatch.setattr(kb_mod, "run_kb_search", _search)
    monkeypatch.setattr(kb_mod, "run_kb_get", _get)

    boom = FakeExecutor()
    boom.ssh_forbidden = True

    allow = resolve_allowed_tools(list(DEFAULT_ALLOWED_TOOLS))
    text = (
        "先查知识库再取全文\n"
        + _wrap_tool_block('{"tool":"kb_search","q":"nginx 502","mode":"kb","limit":3}')
        + _wrap_tool_block('{"tool":"kb_get","entry_id":"nginx502scene"}')
    )
    calls = parse_remote_tool_calls(text, allowed_tools=allow)
    assert [c.tool for c in calls] == ["kb_search", "kb_get"]
    assert all(call_needs_confirmation(c) is False for c in calls)
    assert all(is_plugin_tool(c.tool) for c in calls)

    tr_search = await _exec(calls[0], executor=boom)
    assert tr_search.ok is True
    hits = (tr_search.data or {}).get("results") or []
    assert hits
    eid = hits[0].get("entry_id") or "nginx502scene"

    get_call = RemoteToolCall(
        tool="kb_get",
        raw={"tool": "kb_get", "entry_id": eid},
    )
    tr_get = await _exec(get_call, executor=boom)
    assert tr_get.ok is True
    assert "worker_connections" in str((tr_get.data or {}).get("remediation") or "")
    assert boom.calls == []


@pytest.mark.asyncio
async def test_scene_b2_search_knowledge_then_get_content(kb_store, monkeypatch):
    """search_knowledge(kb) → get_content(full_id) 统一调度链。"""
    import chibycore.knowledge_hub.tool_api as kb_api

    real_search = kb_api.run_kb_search
    real_get = kb_api.run_kb_get

    monkeypatch.setattr(
        kb_api,
        "run_kb_search",
        lambda **kw: real_search(**{**kw, "storage": kb_store}),
    )
    monkeypatch.setattr(
        kb_api,
        "run_kb_get",
        lambda **kw: real_get(**{**kw, "storage": kb_store}),
    )

    boom = FakeExecutor()
    boom.ssh_forbidden = True

    sk = RemoteToolCall(
        tool="search_knowledge",
        raw={
            "tool": "search_knowledge",
            "q": "nginx 502",
            "sources": ["kb"],
            "limit": 5,
        },
    )
    assert is_plugin_tool("search_knowledge")
    assert call_needs_confirmation(sk) is False
    tr = await _exec(sk, executor=boom)
    assert tr.ok is True, (tr.error, tr.error_code)
    results = (tr.data or {}).get("results") or []
    assert results, tr.data
    full_id = results[0].get("full_id")
    assert full_id and str(full_id).startswith("kb:")

    gc = RemoteToolCall(
        tool="get_content",
        raw={"tool": "get_content", "full_id": full_id},
    )
    assert is_plugin_tool("get_content")
    tr2 = await _exec(gc, executor=boom)
    assert tr2.ok is True, (tr2.error, tr2.error_code)
    body = str((tr2.data or {}).get("remediation") or (tr2.data or {}).get("text") or "")
    assert "worker_connections" in body or "nginx" in body.lower()
    assert boom.calls == []


# ── Story C：排障取证链（只读主机） ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_scene_c_triage_readonly_chain():
    """host_list → list_dir → grep → logs；全部免确认。"""
    tools = ["host_list", "remote_list_dir", "remote_grep", "remote_logs"]
    for tid in tools:
        assert is_plugin_tool(tid), tid

    ex = FakeExecutor(
        replies=[
            "app.conf\nerror.log\n",
            "error.log:42: upstream timed out\n",
            "2026-07-27 ERROR upstream timed out\n",
        ]
    )

    boom = FakeExecutor()
    boom.ssh_forbidden = True
    tr0 = await _exec(
        RemoteToolCall(tool="host_list", raw={"tool": "host_list"}),
        executor=boom,
    )
    assert tr0.ok and (tr0.data or {}).get("count") == 2

    steps = [
        RemoteToolCall(
            tool="remote_list_dir",
            host="web1",
            path="/var/log/nginx",
            raw={"tool": "remote_list_dir", "host": "web1", "path": "/var/log/nginx"},
        ),
        RemoteToolCall(
            tool="remote_grep",
            host="web1",
            path="/var/log/nginx",
            pattern="timed out",
            raw={
                "tool": "remote_grep",
                "host": "web1",
                "path": "/var/log/nginx",
                "pattern": "timed out",
            },
        ),
        RemoteToolCall(
            tool="remote_logs",
            host="web1",
            path="/var/log/nginx/error.log",
            lines=80,
            raw={
                "tool": "remote_logs",
                "host": "web1",
                "path": "/var/log/nginx/error.log",
                "lines": 80,
            },
        ),
    ]
    for call in steps:
        assert call_needs_confirmation(call) is False
        tr = await _exec(call, executor=ex)
        assert tr.ok is True, (call.tool, tr.error, tr.error_code)
        assert tr.error_code != "host_required"
    assert len(ex.calls) == 3
    assert any("timed out" in str(c.get("command") or "") for c in ex.calls)


# ── Story D：备份 → 写入 → 回滚 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scene_d_backup_write_restore_rollback():
    """remote_backup → write(confirm) → restore(confirm)；rollback 别名归一。"""
    for tid in ("remote_backup", "remote_write_file", "remote_restore", "remote_rollback"):
        assert is_plugin_tool(tid), tid

    ex = FakeExecutor(
        replies=[
            "backup:/var/backups/app.conf.bak\n",
            "wrote\n",
            "restored\n",
            "rolled\n",
        ]
    )
    backup_path = "/var/backups/app.conf.bak"

    bak = RemoteToolCall(
        tool="remote_backup",
        host="web1",
        path="/etc/app.conf",
        raw={"tool": "remote_backup", "host": "web1", "path": "/etc/app.conf"},
    )
    assert call_needs_confirmation(bak) is False
    assert (await _exec(bak, executor=ex)).ok is True

    write = RemoteToolCall(
        tool="remote_write_file",
        host="web1",
        path="/etc/app.conf",
        content="broken=1\n",
        raw={
            "tool": "remote_write_file",
            "host": "web1",
            "path": "/etc/app.conf",
            "content": "broken=1\n",
        },
    )
    assert call_needs_confirmation(write) is True
    pending_w = remote_tool_call_to_pending_dict(write)
    restored_w = remote_tool_call_from_pending_dict(pending_w)
    assert restored_w is not None
    assert (await _exec(restored_w, executor=ex)).ok is True

    restore = RemoteToolCall(
        tool="remote_restore",
        host="web1",
        path="/etc/app.conf",
        backup_path=backup_path,
        raw={
            "tool": "remote_restore",
            "host": "web1",
            "path": "/etc/app.conf",
            "backup_path": backup_path,
        },
    )
    assert call_needs_confirmation(restore) is True
    pending_r = remote_tool_call_to_pending_dict(restore)
    assert pending_r.get("backup_path") == backup_path
    restored_r = remote_tool_call_from_pending_dict(pending_r)
    assert restored_r is not None
    assert restored_r.backup_path == backup_path
    assert (await _exec(restored_r, executor=ex)).ok is True

    # 别名：parse remote_rollback → remote_restore
    allow = resolve_allowed_tools(list(DEFAULT_ALLOWED_TOOLS))
    text = _wrap_tool_block(
        '{"tool":"remote_rollback","host":"web1","path":"/etc/app.conf",'
        f'"backup_path":"{backup_path}"}}'
    )
    parsed = parse_remote_tool_calls(text, allowed_tools=allow)
    assert len(parsed) == 1
    assert parsed[0].tool == "remote_restore"
    assert call_needs_confirmation(parsed[0]) is True
    assert (await _exec(parsed[0], executor=ex)).ok is True
    # write 可能触发自动备份，调用次数 ≥4（bak + write[+auto] + restore + rollback）
    assert len(ex.calls) >= 4


# ── Story E：Agent 多工具消息 + 市场元数据 ──────────────────────────────────


@pytest.mark.asyncio
async def test_scene_e_agent_multi_tool_message_and_marketplace(doc_env, monkeypatch):
    """一条回复里多块 REMOTE_TOOL：echo → doc_search；旁路断言 Phase6 catalog。"""
    store, ingester, search = doc_env
    f = store.root_dir / "ops.md"
    f.write_text(
        "# 磁盘告警处理\n\n" + ("当 inode 耗尽时清理临时目录并扩容。" * 20),
        encoding="utf-8",
    )
    assert ingester.ingest_file(f, async_if_large=False)["ok"]

    import terminal.mobile.doc_tools as doc_mod

    real_search = doc_mod.run_doc_search

    def _patched(*, q, limit=8, storage=None, searcher=None, search=None, **_kw):
        return real_search(q=q, limit=limit, storage=store, search=search)

    monkeypatch.setattr(doc_mod, "run_doc_search", _patched)

    allow = resolve_allowed_tools(list(DEFAULT_ALLOWED_TOOLS))
    text = (
        "我先确认工具可达，再查文档。\n"
        + _wrap_tool_block('{"tool":"example_echo","text":"plugin-chain"}')
        + _wrap_tool_block('{"tool":"doc_search","q":"磁盘告警","limit":3}')
    )
    calls = parse_remote_tool_calls(text, allowed_tools=allow)
    assert [c.tool for c in calls] == ["example_echo", "doc_search"]
    assert all(is_plugin_tool(c.tool) for c in calls)

    boom = FakeExecutor()
    boom.ssh_forbidden = True
    results = []
    for call in calls:
        assert call_needs_confirmation(call) is False
        tr = await _exec(call, executor=boom)
        assert tr.ok is True, (call.tool, tr.error, tr.error_code)
        results.append(tr)
    assert "plugin-chain" in (results[0].stdout or "")
    assert (results[1].data or {}).get("total", 0) >= 1
    assert boom.calls == []

    cat = build_tools_catalog()
    assert cat.get("phase") == 6
    plugin_ids = {p.get("id") for p in (cat.get("plugins") or [])}
    assert "example_echo" in plugin_ids
    assert "doc_search" in plugin_ids
    packs = {p.get("id") for p in (cat.get("packs") or [])}
    assert "document" in packs or "example" in packs
