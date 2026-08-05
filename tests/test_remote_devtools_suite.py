"""ADR-0003 阶段 3：remote_grep/diff/backup/syntax/logs/run。"""

from __future__ import annotations

import pytest

from terminal.mobile.headless_exec import FakeHeadlessExecutor
from terminal.mobile.remote_devtools import (
    enrich_tool_result_data,
    parse_backup_stdout,
    parse_diff_stdout,
    parse_grep_stdout,
    parse_syntax_stdout,
)
from terminal.mobile.remote_tools import (
    FILE_ALWAYS_CONFIRM,
    FILE_READONLY_TOOLS,
    RemoteToolCall,
    build_file_tool_command,
    call_needs_confirmation,
    execute_remote_tool_call,
    parse_remote_tool_calls,
)


def test_parse_and_confirm_policy_for_devtools():
    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_grep","host":"h1","path":"C:\\\\Open\\\\Api",'
        '"pattern":"def match_route","context":2}\n'
        "<<<END_REMOTE_TOOL>>>\n"
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_search","host":"h1","path":"/tmp","pattern":"TODO"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_restore","host":"h1","path":"/tmp/a.py"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_run","host":"h1","command":"pytest -q","stream":true,"timeout":120}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 4
    assert calls[0].tool == "remote_grep"
    assert calls[0].pattern == "def match_route"
    assert calls[0].context == 2
    assert calls[1].tool == "remote_grep"  # alias
    assert calls[2].tool == "remote_restore"
    assert calls[3].tool == "remote_run"
    assert calls[3].stream is True
    assert calls[3].timeout_sec == 120

    assert "remote_grep" in FILE_READONLY_TOOLS
    assert "remote_backup" in FILE_READONLY_TOOLS
    assert "remote_restore" in FILE_ALWAYS_CONFIRM
    assert call_needs_confirmation(calls[0]) is False
    assert call_needs_confirmation(calls[2], confirm_changes=False) is True


def test_build_grep_logs_backup_diff_syntax_commands():
    g_cmd, g_err = build_file_tool_command(
        RemoteToolCall(
            tool="remote_grep",
            path=r"C:\Open\Api\src",
            pattern="def match_route",
            context=2,
            max_hits=20,
        ),
        conn_type="winrm",
    )
    assert g_err is None
    assert "Select-String" in g_cmd
    assert "ConvertTo-Json" in g_cmd

    l_cmd, l_err = build_file_tool_command(
        RemoteToolCall(
            tool="remote_logs",
            path=r"C:\Open\Api\logs\app.log",
            lines=50,
            filter="ERROR",
        ),
        conn_type="winrm",
    )
    assert l_err is None
    assert "Get-Content" in l_cmd
    assert "ERROR" in l_cmd

    b_cmd, b_err = build_file_tool_command(
        RemoteToolCall(tool="remote_backup", path=r"C:\Open\Api\src\store.py"),
        conn_type="winrm",
    )
    assert b_err is None
    assert ".hermes_backups" in b_cmd
    assert "backup_path=" in b_cmd

    d_cmd, d_err = build_file_tool_command(
        RemoteToolCall(tool="remote_diff", path=r"C:\Open\Api\src\middleware.py"),
        conn_type="winrm",
    )
    assert d_err is None
    assert "source=git" in d_cmd or "git" in d_cmd
    assert ".hermes_backups" in d_cmd

    s_cmd, s_err = build_file_tool_command(
        RemoteToolCall(
            tool="remote_syntax_check",
            path="/tmp/a.py",
            lang="python",
        ),
        conn_type="ssh",
    )
    assert s_err is None
    assert "ast.parse" in s_cmd

    r_cmd, r_err = build_file_tool_command(
        RemoteToolCall(
            tool="remote_restore",
            path="/tmp/a.py",
            backup_path="/home/u/.hermes_backups/x.bak",
        ),
        conn_type="ssh",
    )
    assert r_err is None
    assert "cp -a" in r_cmd


def test_parse_structured_stdout_helpers():
    hits = parse_grep_stdout(
        '[{"file":"a.py","line":10,"content":"def x():","context_before":["#"],'
        '"context_after":["  pass"]}]'
    )
    assert len(hits) == 1
    assert hits[0]["file"] == "a.py"
    assert hits[0]["line"] == 10

    classic = parse_grep_stdout("src/a.py:3:hello world\nsrc/b.py:9:bye")
    assert len(classic) == 2
    assert classic[0]["line"] == 3

    syn = parse_syntax_stdout(
        '{"ok":false,"line":2,"col":1,"msg":"invalid syntax"}', exit_code=1
    )
    assert syn["ok"] is False
    assert syn["line"] == 2

    bak = parse_backup_stdout(
        "backup_ok path=/tmp/a.py\nbackup_path=/home/u/.hermes_backups/ab.2026.bak\n"
    )
    assert bak["backup_path"].endswith(".bak")

    diff = parse_diff_stdout(
        "source=backup\nbackup_path=/x.bak\n----- DIFF -----\n-old\n+new\n----- END -----\n"
    )
    assert diff["source"] == "backup"
    assert diff["changed"] is True
    assert "-old" in diff["diff"]

    data = enrich_tool_result_data(
        "remote_logs", "ERROR boom\nINFO ok\n", exit_code=0
    )
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_remote_run_stream_chunk_invoked():
    call = RemoteToolCall(
        tool="remote_run",
        host="h1",
        command="echo hello",
        stream=True,
    )
    chunks: list[tuple[str, str]] = []

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"

    tr = await execute_remote_tool_call(
        call,
        executor=FakeHeadlessExecutor(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
        stream_chunk=lambda s, c: chunks.append((s, c)),
    )
    assert tr.ok is True
    assert tr.data.get("streamed") is True
    assert chunks  # Fake 按行回调
    assert any("hello" in c or "echo" in c or "(ok)" in c for _, c in chunks)


@pytest.mark.asyncio
async def test_remote_grep_enriches_data_from_stdout(monkeypatch):
    call = RemoteToolCall(
        tool="remote_grep",
        host="h1",
        path="/tmp/src",
        pattern="foo",
    )

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"

    class Ex:
        async def run(self, host_id, command, **kwargs):
            from terminal.mobile.models import ExecResult

            return ExecResult(
                ok=True,
                host_id=host_id,
                command=command,
                exit_code=0,
                stdout_tail='[{"file":"/tmp/src/a.py","line":1,"content":"foo()"}]',
                stderr_tail="",
                duration_ms=1,
                fake=True,
            )

    tr = await execute_remote_tool_call(
        call,
        executor=Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is True
    assert tr.data.get("count") == 1
    assert tr.data["hits"][0]["file"] == "/tmp/src/a.py"


@pytest.mark.asyncio
async def test_write_file_auto_backup_attached(monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_AUTO_BACKUP", "1")
    call = RemoteToolCall(
        tool="remote_write_file",
        host="h1",
        path="/tmp/a.txt",
        content="hi",
    )

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"

    runs: list[str] = []

    class Ex:
        async def run(self, host_id, command, **kwargs):
            from terminal.mobile.models import ExecResult

            runs.append(command)
            if "hermes_backups" in command or "backup_path=" in command:
                out = "backup_ok path=/tmp/a.txt\nbackup_path=/tmp/.hermes_backups/x.bak\n"
            else:
                out = "wrote 2 bytes / 1 lines -> /tmp/a.txt\n"
            return ExecResult(
                ok=True,
                host_id=host_id,
                command=command,
                exit_code=0,
                stdout_tail=out,
                stderr_tail="",
                duration_ms=1,
                fake=True,
            )

    tr = await execute_remote_tool_call(
        call,
        executor=Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is True
    assert len(runs) >= 2
    assert tr.data.get("auto_backup", {}).get("backup_path")
