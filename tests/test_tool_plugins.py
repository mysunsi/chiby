"""工具目录插件化：发现 / 白名单 / 执行。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from terminal.mobile.remote_tools import (
    DEFAULT_ALLOWED_TOOLS,
    RemoteToolCall,
    call_needs_confirmation,
    execute_remote_tool_call,
    parse_remote_tool_calls,
    resolve_allowed_tools,
)
from terminal.tools_plugin_loader import (
    discover_plugins,
    get_registry,
    is_plugin_tool,
    plugin_tool_ids,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_plugins():
    reset_registry()
    yield
    reset_registry()


def test_discover_example_echo():
    reg = discover_plugins(force=True)
    assert "example_echo" in reg.tools
    assert "host_list" in reg.tools
    assert is_plugin_tool("example_echo")
    assert is_plugin_tool("host_list")
    p = reg.get("example_echo")
    assert p is not None
    assert p.read_only is True
    assert p.needs_confirmation is False
    hl = reg.get("host_list")
    assert hl is not None
    assert hl.read_only is True


def test_resolve_allowed_merges_plugins():
    tools = resolve_allowed_tools(list(DEFAULT_ALLOWED_TOOLS), auto_merge_plugins=True)
    assert "example_echo" in tools
    assert "kb_search" in tools


@pytest.mark.asyncio
async def test_execute_plugin_no_ssh():
    reset_registry()
    discover_plugins(force=True)
    call = RemoteToolCall(
        tool="example_echo",
        raw={"tool": "example_echo", "text": "plugin-path"},
    )

    async def _boom(*_a, **_k):
        raise AssertionError("SSH must not run for plugin tools")

    tr = await execute_remote_tool_call(
        call,
        executor=_boom,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
    )
    assert tr.ok is True
    assert "plugin-path" in (tr.stdout or "")


@pytest.mark.asyncio
async def test_execute_host_list_plugin():
    reset_registry()
    discover_plugins(force=True)
    call = RemoteToolCall(tool="host_list", raw={"tool": "host_list"})

    async def _boom(*_a, **_k):
        raise AssertionError("SSH must not run for host_list")

    hosts = [
        {"id": "h1", "hostname": "web1", "host": "10.0.0.1"},
        {"id": "h2", "hostname": "db1", "host": "10.0.0.2"},
    ]
    tr = await execute_remote_tool_call(
        call,
        executor=_boom,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
        list_visible_hosts=lambda: hosts,
    )
    assert tr.ok is True
    assert (tr.data or {}).get("count") == 2
    assert "可见主机 2 台" in (tr.stdout or "")


def test_parse_plugin_tool():
    reset_registry()
    discover_plugins(force=True)
    allow = resolve_allowed_tools(None)
    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"example_echo","text":"hi"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text, allowed_tools=allow)
    assert len(calls) == 1
    assert call_needs_confirmation(calls[0]) is False


def test_plugins_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_TOOL_PLUGINS", "0")
    reset_registry()
    reg = discover_plugins(force=True)
    assert "example_echo" not in reg.tools
    monkeypatch.delenv("OPS_TOOL_PLUGINS", raising=False)
    reset_registry()


def test_temp_plugin_dir(monkeypatch, tmp_path):
    root = tmp_path / "plugins"
    tool = root / "demo_ping"
    tool.mkdir(parents=True)
    (tool / "manifest.yaml").write_text(
        """
name: demo_ping
description: ping demo
type: local_readonly
host_required: false
status: approved
parameters:
  - name: msg
    type: string
    required: true
security:
  needs_confirmation: false
  read_only: true
executor:
  entry: run
usage_example: |
  {"tool":"demo_ping","msg":"pong"}
""".strip(),
        encoding="utf-8",
    )
    (tool / "handler.py").write_text(
        """
def run(params, context):
    return {"ok": True, "echo": str(params.get("msg") or ""), "result": "pong:" + str(params.get("msg") or "")}

def format_result(data):
    return str(data.get("result") or "")
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_TOOL_PLUGINS_DIR", str(root))
    monkeypatch.setenv("OPS_TOOL_PLUGINS", "1")
    reset_registry()
    reg = discover_plugins(root=root, force=True)
    assert "demo_ping" in reg.tools
    assert "demo_ping" in plugin_tool_ids()
    allow = resolve_allowed_tools(["ssh_execute"], auto_merge_plugins=True)
    assert "demo_ping" in allow


def test_discover_kb_doc_plugins():
    reg = discover_plugins(force=True)
    for name in (
        "kb_search",
        "kb_get",
        "kb_ingest",
        "doc_search",
        "doc_get",
        "search_knowledge",
        "get_content",
    ):
        assert name in reg.tools, name
        assert is_plugin_tool(name), name
    ingest = reg.get("kb_ingest")
    assert ingest is not None
    assert ingest.needs_confirmation is True
    assert ingest.read_only is False
    assert reg.get("kb_search").read_only is True
    assert reg.get("doc_search").read_only is True


@pytest.mark.asyncio
async def test_execute_kb_search_via_plugin(tmp_path, monkeypatch):
    reset_registry()
    discover_plugins(force=True)
    assert is_plugin_tool("kb_search")
    call = RemoteToolCall(
        tool="kb_search",
        raw={"tool": "kb_search", "q": "nginx", "mode": "kb", "limit": 3},
    )

    async def _boom(*_a, **_k):
        raise AssertionError("SSH must not run for kb_search")

    tr = await execute_remote_tool_call(
        call,
        executor=_boom,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
    )
    # 空库也可能 ok=True hits=[]；关键是走插件且不抛
    assert tr.error_code != "plugin_not_found"
    assert tr.command == "kb_search"


def test_kb_ingest_needs_confirmation_from_manifest():
    reset_registry()
    discover_plugins(force=True)
    call = RemoteToolCall(
        tool="kb_ingest",
        raw={"tool": "kb_ingest", "title": "t", "symptom": "s"},
    )
    assert call_needs_confirmation(call) is True


def test_catalog_includes_plugins():
    reset_registry()
    discover_plugins(force=True)
    from terminal.tools_catalog import build_tools_catalog

    cat = build_tools_catalog()
    assert cat["ok"] is True
    ids = {p["id"] for p in cat.get("plugins") or []}
    assert "example_echo" in ids
    assert "host_list" in ids
    assert "kb_search" in ids
    assert "doc_search" in ids
    assert "search_knowledge" in ids
    assert "remote_read_file" in ids
    assert "remote_list_dir" in ids
    assert "remote_grep" in ids
    official_ids = {o["id"] for o in cat.get("official") or []}
    # 已插件化的从 official 去重
    assert "kb_search" not in official_ids
    assert "doc_search" not in official_ids
    assert "example_echo" not in official_ids
    assert "remote_read_file" not in official_ids
    assert "remote_list_dir" not in official_ids
    assert "remote_grep" not in official_ids


def test_discover_host_readonly_plugins():
    reg = discover_plugins(force=True)
    for name in (
        "remote_read_file",
        "remote_list_dir",
        "remote_grep",
        "remote_search",
        "remote_diff",
        "remote_logs",
        "remote_backup",
        "remote_syntax_check",
    ):
        assert name in reg.tools, name
        p = reg.get(name)
        assert p is not None
        assert p.host_required is True
        assert p.read_only is True
        assert p.needs_confirmation is False
        assert p.tool_type == "host_readonly"
        assert is_plugin_tool(name)


@pytest.mark.asyncio
async def test_execute_host_readonly_plugins_via_kernel():
    """Phase 3 只读文件面：插件 → delegate → 内核 → executor。"""
    reset_registry()
    discover_plugins(force=True)

    seen = {"n": 0}

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "plugin-host-ok\n"
        stderr_tail = ""
        error = ""
        duration_ms = 2

    class Ex:
        async def run(self, host_id, command, **kwargs):
            seen["n"] += 1
            return Er()

    host = type(
        "H",
        (),
        {"conn_type": "ssh", "password": "x", "ssh_private_key_path": None},
    )()

    cases = [
        RemoteToolCall(
            tool="remote_list_dir",
            host="h1",
            path="/tmp",
            raw={"tool": "remote_list_dir", "host": "h1", "path": "/tmp"},
        ),
        RemoteToolCall(
            tool="remote_read_file",
            host="h1",
            path="/tmp/a.txt",
            max_bytes=100,
            raw={
                "tool": "remote_read_file",
                "host": "h1",
                "path": "/tmp/a.txt",
                "max_bytes": 100,
            },
        ),
        RemoteToolCall(
            tool="remote_grep",
            host="h1",
            path="/tmp",
            pattern="hello",
            raw={
                "tool": "remote_grep",
                "host": "h1",
                "path": "/tmp",
                "pattern": "hello",
            },
        ),
        RemoteToolCall(
            tool="remote_search",
            host="h1",
            path="/tmp",
            pattern="TODO",
            raw={
                "tool": "remote_search",
                "host": "h1",
                "path": "/tmp",
                "pattern": "TODO",
            },
        ),
        RemoteToolCall(
            tool="remote_diff",
            host="h1",
            path="/tmp/a.conf",
            raw={"tool": "remote_diff", "host": "h1", "path": "/tmp/a.conf"},
        ),
        RemoteToolCall(
            tool="remote_logs",
            host="h1",
            path="/var/log/app.log",
            lines=50,
            raw={
                "tool": "remote_logs",
                "host": "h1",
                "path": "/var/log/app.log",
                "lines": 50,
            },
        ),
        RemoteToolCall(
            tool="remote_backup",
            host="h1",
            path="/tmp/a.conf",
            raw={"tool": "remote_backup", "host": "h1", "path": "/tmp/a.conf"},
        ),
        RemoteToolCall(
            tool="remote_syntax_check",
            host="h1",
            path="/tmp/a.py",
            lang="python",
            raw={
                "tool": "remote_syntax_check",
                "host": "h1",
                "path": "/tmp/a.py",
                "lang": "python",
            },
        ),
    ]
    for call in cases:
        tr = await execute_remote_tool_call(
            call,
            executor=Ex(),
            host_allowed=lambda _h: True,
            resolve_host=lambda _h: host,
            conn_type_for=lambda _h: "ssh",
        )
        assert tr.ok is True, (call.tool, tr.error, tr.error_code)
        assert "plugin-host-ok" in (tr.stdout or "")
        assert call_needs_confirmation(call) is False
    assert seen["n"] >= len(cases)


def test_discover_remote_write_file_host_write():
    reg = discover_plugins(force=True)
    assert "remote_write_file" in reg.tools
    p = reg.get("remote_write_file")
    assert p is not None
    assert p.host_required is True
    assert p.tool_type == "host_write"
    assert p.needs_confirmation is True
    assert p.is_mutate is True
    assert p.read_only is False
    assert "path" in p.confirm_fields
    assert "preview" in p.confirm_fields


@pytest.mark.asyncio
async def test_execute_remote_write_file_via_plugin_needs_confirm():
    reset_registry()
    discover_plugins(force=True)

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "wrote ok\n"
        stderr_tail = ""
        error = ""
        duration_ms = 2

    class Ex:
        async def run(self, host_id, command, **kwargs):
            return Er()

    call = RemoteToolCall(
        tool="remote_write_file",
        host="h1",
        path="/tmp/hello.txt",
        content="hello",
        raw={
            "tool": "remote_write_file",
            "host": "h1",
            "path": "/tmp/hello.txt",
            "content": "hello",
        },
    )
    assert call_needs_confirmation(call) is True
    host = type(
        "H",
        (),
        {"conn_type": "ssh", "password": "x", "ssh_private_key_path": None},
    )()
    tr = await execute_remote_tool_call(
        call,
        executor=Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: host,
        conn_type_for=lambda _h: "ssh",
    )
    assert tr.ok is True, (tr.error, tr.error_code)
    assert is_plugin_tool("remote_write_file")


@pytest.mark.asyncio
async def test_phase4_write_plugins_confirm_and_execute():
    reset_registry()
    discover_plugins(force=True)
    for name in (
        "remote_write_file",
        "remote_mkdir",
        "remote_remove",
        "remote_restore",
        "remote_rollback",
    ):
        assert is_plugin_tool(name), name
        p = discover_plugins().get(name)
        assert p is not None and p.tool_type == "host_write"
        assert p.needs_confirmation is True
        assert p.is_mutate is True

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "mutate-ok\n"
        stderr_tail = ""
        error = ""
        duration_ms = 1

    class Ex:
        async def run(self, host_id, command, **kwargs):
            return Er()

    host = type(
        "H",
        (),
        {"conn_type": "ssh", "password": "x", "ssh_private_key_path": None},
    )()

    mkdir = RemoteToolCall(
        tool="remote_mkdir",
        host="h1",
        path="/tmp/d",
        raw={"tool": "remote_mkdir", "host": "h1", "path": "/tmp/d"},
    )
    assert call_needs_confirmation(mkdir, confirm_changes=True) is True
    assert call_needs_confirmation(mkdir, confirm_changes=False) is False

    cases = [
        mkdir,
        RemoteToolCall(
            tool="remote_remove",
            host="h1",
            path="/tmp/old",
            recursive=True,
            raw={
                "tool": "remote_remove",
                "host": "h1",
                "path": "/tmp/old",
                "recursive": True,
            },
        ),
        RemoteToolCall(
            tool="remote_restore",
            host="h1",
            path="/tmp/a.conf",
            raw={"tool": "remote_restore", "host": "h1", "path": "/tmp/a.conf"},
        ),
    ]
    for call in cases:
        if call.tool != "remote_mkdir":
            assert call_needs_confirmation(call) is True
        tr = await execute_remote_tool_call(
            call,
            executor=Ex(),
            host_allowed=lambda _h: True,
            resolve_host=lambda _h: host,
            conn_type_for=lambda _h: "ssh",
        )
        assert tr.ok is True, (call.tool, tr.error, tr.error_code)


@pytest.mark.asyncio
async def test_phase5_remote_run_plugin():
    reset_registry()
    discover_plugins(force=True)
    assert is_plugin_tool("remote_run")
    p = discover_plugins().get("remote_run")
    assert p is not None
    assert p.tool_type == "host_command"
    assert "command" in p.confirm_fields

    # confirm_mode=command_content → 不强制确认，按命令内容判定
    safe = RemoteToolCall(
        tool="remote_run",
        host="h1",
        command="df -h",
        raw={"tool": "remote_run", "host": "h1", "command": "df -h"},
    )
    assert call_needs_confirmation(safe, confirm_changes=True) is False

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "Filesystem ...\n"
        stderr_tail = ""
        error = ""
        duration_ms = 1

    class Ex:
        async def run(self, host_id, command, **kwargs):
            assert "df -h" in command
            return Er()

    host = type(
        "H",
        (),
        {"conn_type": "ssh", "password": "x", "ssh_private_key_path": None},
    )()
    tr = await execute_remote_tool_call(
        safe,
        executor=Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: host,
        conn_type_for=lambda _h: "ssh",
    )
    assert tr.ok is True, (tr.error, tr.error_code)


@pytest.mark.asyncio
async def test_phase5_ssh_winrm_execute_plugins():
    reset_registry()
    discover_plugins(force=True)
    for name in ("ssh_execute", "winrm_execute", "remote_run"):
        assert is_plugin_tool(name), name
        p = discover_plugins().get(name)
        assert p is not None and p.tool_type == "host_command"

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "ok\n"
        stderr_tail = ""
        error = ""
        duration_ms = 1

    class Ex:
        async def run(self, host_id, command, **kwargs):
            return Er()

    ssh_host = type(
        "H",
        (),
        {"conn_type": "ssh", "password": "x", "ssh_private_key_path": None},
    )()
    win_host = type(
        "H",
        (),
        {"conn_type": "winrm", "password": "x", "ssh_private_key_path": None},
    )()

    ssh_call = RemoteToolCall(
        tool="ssh_execute",
        host="h1",
        command="uptime",
        raw={"tool": "ssh_execute", "host": "h1", "command": "uptime"},
    )
    assert call_needs_confirmation(ssh_call, confirm_changes=True) is False
    tr = await execute_remote_tool_call(
        ssh_call,
        executor=Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: ssh_host,
        conn_type_for=lambda _h: "ssh",
    )
    assert tr.ok is True, (tr.error, tr.error_code)

    win_call = RemoteToolCall(
        tool="winrm_execute",
        host="w1",
        command="hostname",
        raw={"tool": "winrm_execute", "host": "w1", "command": "hostname"},
    )
    tr2 = await execute_remote_tool_call(
        win_call,
        executor=Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: win_host,
        conn_type_for=lambda _h: "winrm",
    )
    assert tr2.ok is True, (tr2.error, tr2.error_code)


def test_marketplace_catalog_phase6_fields():
    from terminal.tools_catalog import build_skill_packs, build_tools_catalog, filter_catalog
    from terminal.tools_plugin_loader import get_plugin_detail

    reset_registry()
    discover_plugins(force=True)
    cat = build_tools_catalog()
    assert cat.get("phase") == 6
    assert cat.get("pack_count", 0) >= 1
    assert isinstance(cat.get("packs"), list)
    plugins = cat.get("plugins") or []
    assert plugins
    sample = next((p for p in plugins if p.get("id") == "host_list"), plugins[0])
    assert sample.get("version")
    assert sample.get("skill_pack") or sample.get("category")
    assert "dependencies" in sample
    assert sample.get("loaded") is True or sample.get("status") == "loaded"

    packs = build_skill_packs(plugins)
    assert packs
    assert all("tool_ids" in p for p in packs)

    filtered = filter_catalog(cat, pack=str(sample.get("skill_pack") or sample.get("category")))
    assert filtered.get("filtered") is True
    assert all(
        str(p.get("skill_pack") or p.get("category"))
        == str(sample.get("skill_pack") or sample.get("category"))
        for p in (filtered.get("plugins") or [])
    )

    detail = get_plugin_detail("host_list")
    assert detail is not None
    assert detail.get("id") == "host_list"
    assert detail.get("version")
    sk = get_plugin_detail("search_knowledge")
    assert sk is not None
    deps = sk.get("dependencies") or []
    assert any(d.get("id") == "kb_search" for d in deps)
