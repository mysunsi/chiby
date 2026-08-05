"""ADR-0003 remote_tools：配置、路由判定、契约解析、凭据拒绝。"""

from __future__ import annotations

from pathlib import Path

import pytest

from terminal.hermes_bridge.config import HermesBridgeConfig, load_hermes_bridge_config
from terminal.mobile.remote_tools import (
    call_needs_confirmation,
    dual_path_should_ignore_ops,
    execute_remote_tool_call,
    parse_remote_tool_calls,
    reject_secret_params,
    resolve_exec_path,
    RemoteToolCall,
)


def test_remote_tools_config_default_off(tmp_path: Path):
    p = tmp_path / "hermes_bridge.yaml"
    p.write_text("enabled: true\nplan_only: true\n", encoding="utf-8")
    cfg = load_hermes_bridge_config(p)
    assert cfg.remote_tools.enabled is False
    assert cfg.remote_tools.prefer_over_ops_plan is True


def test_remote_tools_config_enabled(tmp_path: Path):
    p = tmp_path / "hermes_bridge.yaml"
    p.write_text(
        "enabled: true\n"
        "remote_tools:\n"
        "  enabled: true\n"
        "  prefer_over_ops_plan: true\n"
        "  allowed_tools: [host_list, ssh_execute]\n",
        encoding="utf-8",
    )
    cfg = load_hermes_bridge_config(p)
    assert cfg.remote_tools.enabled is True
    assert cfg.remote_tools.allowed_tools == ["host_list", "ssh_execute"]


def test_resolve_exec_path_priority():
    assert resolve_exec_path(enabled=False, has_remote_tools=True, has_ops=True) == "a1"
    assert resolve_exec_path(enabled=True, has_remote_tools=True, has_ops=True) == "a2"
    # 单脑：enabled 时无 REMOTE 不回退 A1
    assert resolve_exec_path(enabled=True, has_remote_tools=False, has_ops=True) == "none"
    assert resolve_exec_path(enabled=True, has_remote_tools=False, has_ops=False) == "none"
    assert dual_path_should_ignore_ops(
        enabled=True, has_remote_tools=True, has_ops=True
    )
    assert dual_path_should_ignore_ops(
        enabled=True, has_remote_tools=False, has_ops=True
    )


def test_reject_secret_params():
    assert reject_secret_params({"host": "a", "command": "df"}) is None
    assert reject_secret_params({"password": "x"}) == "forbidden_param:password"
    assert reject_secret_params({"userPassword": "x"}) == "forbidden_param:userPassword"


def test_parse_remote_tool_and_reject_password_in_block():
    text = (
        "查磁盘\n"
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"ssh_execute","host":"web-01","command":"df -h"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].tool == "ssh_execute"
    assert calls[0].host == "web-01"

    bad = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"ssh_execute","host":"web-01","command":"df","password":"secret"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    assert parse_remote_tool_calls(bad) == []


def test_parse_remote_tools_batch_array():
    text = (
        "<<<REMOTE_TOOLS>>>\n"
        '[{"tool":"ssh_batch","hosts":["a","b"],"command":"uptime"}]\n'
        "<<<END_REMOTE_TOOLS>>>\n"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].hosts == ["a", "b"]


@pytest.mark.asyncio
async def test_execute_permission_denied():
    call = RemoteToolCall(tool="ssh_execute", host="nope", command="df -h")

    class _Ex:
        async def run(self, *a, **k):
            raise AssertionError("should not run")

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: False,
        resolve_host=lambda _h: object(),
    )
    assert tr.ok is False
    assert tr.error_code == "permission_denied"


@pytest.mark.asyncio
async def test_execute_credential_missing():
    call = RemoteToolCall(tool="ssh_execute", host="h1", command="df -h")

    class Host:
        password = None
        ssh_private_key_path = None
        conn_type = "ssh"

    class _Ex:
        async def run(self, *a, **k):
            raise AssertionError("should not run")

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is False
    assert tr.error_code == "credential_missing"


@pytest.mark.asyncio
async def test_execute_ssh_ok_exit_code_zero():
    call = RemoteToolCall(tool="ssh_execute", host="h1", command="free -h")

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "Mem: 3.7Gi available 1.1Gi\n"
        stderr_tail = ""
        error = ""
        duration_ms = 12

    class _Ex:
        async def run(self, host_id, command, **k):
            return Er()

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is True
    assert tr.exit_code == 0
    pub = tr.to_public_dict()
    assert pub["exit_code"] == 0


def test_format_tool_results_for_user_foldable_exec_body():
    from terminal.mobile.remote_tools import (
        RemoteToolResult,
        format_tool_results_for_user,
    )

    text = format_tool_results_for_user(
        [
            RemoteToolResult(
                tool="ssh_execute",
                ok=True,
                host="h1",
                exit_code=0,
                command="free -h",
                stdout="Mem: 1.1Gi available\n",
                duration_ms=10,
            )
        ]
    )
    assert "**远端执行结果**" in text
    assert "<<<EXEC_BODY>>>" in text
    assert "<<<END_EXEC_BODY>>>" in text
    assert "已成功执行" in text
    assert "free -h" in text
    assert "1.1Gi" in text
    # 与智能型对齐：单机不在摘要行塞 [host_id]
    assert "`$ free -h`" in text
    assert "[h1]" not in text
    # 明细在折叠体内，不再裸刷大段 ``` 代码块标题式明细
    assert "执行明细" not in text
    assert "exit=" not in text


def test_format_tool_results_prefers_shell_not_bare_tool_name():
    from terminal.mobile.remote_tools import (
        RemoteToolResult,
        format_tool_results_for_user,
    )

    # 有真实 shell 时展示 shell
    text = format_tool_results_for_user(
        [
            RemoteToolResult(
                tool="remote_remove",
                ok=True,
                host="h1",
                exit_code=0,
                command='rm -rf -- "/tmp/junk"',
                stdout="removed_recursive:/tmp/junk\n",
                data={"path": "/tmp/junk", "recursive": True},
            )
        ]
    )
    assert "`$ rm -rf -- \"/tmp/junk\"`" in text or "rm -rf" in text
    assert text.count("**远端执行结果**") == 1

    # command 退化成工具名时，用 path 拼预览
    text2 = format_tool_results_for_user(
        [
            RemoteToolResult(
                tool="remote_remove",
                ok=True,
                host="h1",
                exit_code=0,
                command="remote_remove",
                stdout="removed recursive: C:\\Users\\x\\ztencentcloud_files",
                data={"path": r"C:\Users\x\ztencentcloud_files", "recursive": True},
            )
        ],
        include_heading=False,
    )
    assert "**远端执行结果**" not in text2
    assert "remote_remove -r" in text2
    assert "ztencentcloud_files" in text2
    assert "<<<EXEC_BODY>>>" in text2


def test_format_tool_results_batch_keeps_host_prefix():
    from terminal.mobile.remote_tools import (
        RemoteToolResult,
        format_tool_results_for_user,
    )

    text = format_tool_results_for_user(
        [
            RemoteToolResult(
                tool="ssh_batch",
                ok=True,
                results=[
                    {
                        "host": "h1",
                        "command": "uptime",
                        "ok": True,
                        "exit_code": 0,
                        "stdout": "up 1 day",
                    }
                ],
            )
        ]
    )
    assert "[h1]" in text
    assert "uptime" in text


def test_call_needs_confirmation_mutate():
    assert call_needs_confirmation(
        RemoteToolCall(tool="host_list")
    ) is False
    assert call_needs_confirmation(
        RemoteToolCall(tool="ssh_execute", command="df -h")
    ) is False
    assert call_needs_confirmation(
        RemoteToolCall(tool="ssh_execute", command="systemctl restart nginx")
    ) is True
    # 全能型：常规受控变更（如 restart）可不确认；毁灭性仍确认
    assert call_needs_confirmation(
        RemoteToolCall(tool="ssh_execute", command="systemctl restart nginx"),
        confirm_changes=False,
    ) is False
    assert call_needs_confirmation(
        RemoteToolCall(tool="ssh_execute", command="rm -rf /"),
        confirm_changes=False,
    ) is True


def test_awk_nr_gt_not_treated_as_redirect():
    """awk 比较 NR>1 不应误判为 shell 重定向写盘。"""
    from terminal.mobile.orchestrator import _REMOTE_WRITE_RE, _is_controlled_mutate

    cmd = (
        "ps aux --sort=-%mem | head -25 | awk "
        "'NR==1{print} NR>1{printf \"%s\\n\",$1}'"
    )
    assert _REMOTE_WRITE_RE.search(cmd) is None
    assert _is_controlled_mutate(cmd) is False
    assert (
        call_needs_confirmation(
            RemoteToolCall(tool="ssh_execute", command=cmd),
            confirm_changes=False,
        )
        is False
    )
    # 真重定向仍须确认
    assert _is_controlled_mutate("echo hi > /tmp/a.txt") is True
    assert _is_controlled_mutate("echo hi >> /tmp/a.txt") is True
    # 只读丢弃 stderr 不确认
    assert _is_controlled_mutate("ps aux 2>/dev/null") is False


def test_parse_mangled_remote_tool_markers():
    text = (
        "查负载\n"
        "REMOTE_TOOL>>>\n"
        '{"tool":"ssh_execute","host":"h1","command":"uptime"}\n'
        "END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].command == "uptime"


def test_parse_loose_tool_json():
    text = '说明一下\n{"tool":"ssh_execute","host":"h1","command":"hostname"}\n'
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].command == "hostname"


def test_parse_file_tools():
    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_list_dir","host":"h1","path":"/home/app"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_write_file","host":"h1","path":"/tmp/a.txt","content":"hello"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 2
    assert calls[0].tool == "remote_list_dir"
    assert calls[0].path == "/home/app"
    assert calls[1].tool == "remote_write_file"
    assert calls[1].content == "hello"
    assert "remote_write_file" in calls[1].shell_text


def test_file_tool_confirmation_policy():
    assert call_needs_confirmation(
        RemoteToolCall(tool="remote_list_dir", host="h1", path="/tmp")
    ) is False
    assert call_needs_confirmation(
        RemoteToolCall(tool="remote_read_file", host="h1", path="/tmp/a")
    ) is False
    assert call_needs_confirmation(
        RemoteToolCall(tool="remote_write_file", host="h1", path="/tmp/a", content="x"),
        confirm_changes=False,
    ) is True
    assert call_needs_confirmation(
        RemoteToolCall(tool="remote_remove", host="h1", path="/tmp/a"),
        confirm_changes=False,
    ) is True
    assert call_needs_confirmation(
        RemoteToolCall(tool="remote_mkdir", host="h1", path="/tmp/d"),
        confirm_changes=False,
    ) is False
    assert call_needs_confirmation(
        RemoteToolCall(tool="remote_mkdir", host="h1", path="/tmp/d"),
        confirm_changes=True,
    ) is True


def test_build_file_tool_command_rejects_root():
    from terminal.mobile.remote_tools import build_file_tool_command

    cmd, err = build_file_tool_command(
        RemoteToolCall(tool="remote_list_dir", path="/"),
        conn_type="ssh",
    )
    assert cmd == ""
    assert err


def test_build_file_tool_write_ssh_base64():
    from terminal.mobile.remote_tools import build_file_tool_command

    cmd, err = build_file_tool_command(
        RemoteToolCall(
            tool="remote_write_file",
            path="/tmp/hello.txt",
            content="hi",
        ),
        conn_type="ssh",
    )
    assert err is None
    assert "base64 -d" in cmd
    assert "/tmp/hello.txt" in cmd


@pytest.mark.asyncio
async def test_execute_remote_list_dir():
    call = RemoteToolCall(tool="remote_list_dir", host="h1", path="/tmp")

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "total 0\n"
        stderr_tail = ""
        error = ""
        duration_ms = 5

    seen = {}

    class _Ex:
        async def run(self, host_id, command, **k):
            seen["cmd"] = command
            return Er()

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is True
    assert "ls -la" in seen["cmd"]
    assert tr.command.startswith("remote_list_dir")


@pytest.mark.asyncio
async def test_execute_remote_write_file_display_not_base64():
    call = RemoteToolCall(
        tool="remote_write_file",
        host="h1",
        path="/tmp/x.txt",
        content="payload",
    )

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "7 /tmp/x.txt\n"
        stderr_tail = ""
        error = ""
        duration_ms = 5

    class _Ex:
        async def run(self, host_id, command, **k):
            assert "base64" in command
            return Er()

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is True
    assert "base64" not in tr.command
    assert "remote_write_file" in tr.command


def test_winrm_write_mkdir_avoid_newitem_literalpath():
    """WinRM 写文件/建目录走 .NET API，避免 New-Item -LiteralPath 兼容坑。"""
    from terminal.mobile.remote_tools import build_file_tool_command

    w_cmd, w_err = build_file_tool_command(
        RemoteToolCall(
            tool="remote_write_file",
            host="h1",
            path=r"C:\some\path\file.txt",
            content="文件内容",
        ),
        conn_type="winrm",
    )
    assert w_err is None
    assert "New-Item" not in w_cmd
    assert "[IO.Directory]::CreateDirectory" in w_cmd
    assert "[IO.File]::WriteAllBytes" in w_cmd
    assert "FromBase64String" in w_cmd
    assert "write digest" in w_cmd

    m_cmd, m_err = build_file_tool_command(
        RemoteToolCall(tool="remote_mkdir", host="h1", path=r"C:\tmp\demo_dir"),
        conn_type="winrm",
    )
    assert m_err is None
    assert "New-Item" not in m_cmd
    assert "[IO.Directory]::CreateDirectory" in m_cmd


def test_remote_read_file_utf8_and_window_params():
    from terminal.mobile.remote_tools import (
        build_file_tool_command,
        parse_remote_tool_calls,
        resolve_read_window,
    )

    calls = parse_remote_tool_calls(
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_read_file","host":"h1","path":"C:\\\\a.py",'
        '"max_bytes":0,"offset":100}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    assert len(calls) == 1
    assert calls[0].max_bytes == 0
    assert calls[0].offset == 100
    max_b, off, tail = resolve_read_window(calls[0])
    assert max_b >= 100_000  # 0 → 硬上限
    assert off == 100
    assert tail == 0

    cmd, err = build_file_tool_command(calls[0], conn_type="winrm")
    assert err is None
    assert "ReadAllText" not in cmd
    assert "UTF8Encoding" in cmd
    assert "ReadAllBytes" in cmd

    tail_call = RemoteToolCall(
        tool="remote_read_file",
        host="h1",
        path=r"C:\a.py",
        tail_lines=80,
    )
    tcmd, terr = build_file_tool_command(tail_call, conn_type="winrm")
    assert terr is None
    assert "ReadAllLines" in tcmd
    assert "UTF8Encoding" in tcmd

    scmd, serr = build_file_tool_command(
        RemoteToolCall(tool="remote_read_file", path="/tmp/a.py", max_bytes=8000),
        conn_type="ssh",
    )
    assert serr is None
    assert "head -c 8000" in scmd


@pytest.mark.asyncio
async def test_execute_remote_write_file_winrm_uses_dotnet():
    call = RemoteToolCall(
        tool="remote_write_file",
        host="h1",
        path=r"C:\tmp\x.txt",
        content="payload",
    )

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "winrm"

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "wrote 7 bytes -> C:\\tmp\\x.txt\n"
        stderr_tail = ""
        error = ""
        duration_ms = 5

    seen = {}

    class _Ex:
        async def run(self, host_id, command, **k):
            seen["cmd"] = command
            assert "New-Item" not in command
            assert "[IO.File]::WriteAllBytes" in command
            return Er()

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
    )
    assert tr.ok is True
    assert "remote_write_file" in tr.command
    assert "New-Item" not in seen["cmd"]


def test_remote_tools_preamble_mentions_file_tools():
    from terminal.mobile.remote_tools import (
        DEFAULT_ALLOWED_TOOLS,
        remote_tools_preamble_addon,
    )

    text = remote_tools_preamble_addon(
        enabled=True, allowed_tools=list(DEFAULT_ALLOWED_TOOLS)
    )
    assert "remote_list_dir" in text
    assert "remote_write_file" in text
    assert "remote_mkdir" in text


@pytest.mark.asyncio
async def test_ssh_batch_filters_winrm_and_emits_batch_scope():
    """混合协议：ssh_batch 只跑 SSH；屏障 scope=batch，勿当整轮结束。"""
    call = RemoteToolCall(
        tool="ssh_batch",
        hosts=["win1", "lin1", "lin2"],
        command="free -h",
    )

    class Host:
        def __init__(self, ct: str, name: str = ""):
            self.password = "x"
            self.ssh_private_key_path = None
            self.conn_type = ct
            self.name = name
            self.host = name

    hosts = {
        "win1": Host("winrm", "Win"),
        "lin1": Host("ssh", "Debian"),
        "lin2": Host("ssh", "Ubuntu"),
    }

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "Mem: ok\n"
        stderr_tail = ""
        error = ""
        duration_ms = 5

    ran: list[str] = []

    class _Ex:
        async def run(self, host_id, command, **k):
            ran.append(host_id)
            return Er()

    events: list[dict] = []

    async def on_prog(ev: dict) -> None:
        events.append(ev)

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda h: hosts[h],
        conn_type_for=lambda h: hosts[h].conn_type,
        on_host_progress=on_prog,
    )
    assert set(ran) == {"lin1", "lin2"}
    assert "win1" not in ran
    assert tr.ok is True
    assert tr.summary.get("total") == 2
    assert any(e.get("type") == "host_panel_barrier" and e.get("scope") == "batch" for e in events)
    done = next(e for e in events if e.get("type") == "host_panel_done")
    assert done.get("scope") == "batch"
    assert done.get("barrier") is False


@pytest.mark.asyncio
async def test_ssh_execute_emits_host_task_progress():
    call = RemoteToolCall(tool="ssh_execute", host="h1", command="free -h")

    class Host:
        password = "x"
        ssh_private_key_path = None
        conn_type = "ssh"
        name = "Debian"
        host = "172.25.87.85"

    class Er:
        ok = True
        exit_code = 0
        stdout_tail = "Mem: 1G\n"
        stderr_tail = ""
        error = ""
        duration_ms = 3

    class _Ex:
        async def run(self, host_id, command, **k):
            return Er()

    events: list[dict] = []

    async def on_prog(ev: dict) -> None:
        events.append(ev)

    tr = await execute_remote_tool_call(
        call,
        executor=_Ex(),
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: Host(),
        on_host_progress=on_prog,
    )
    assert tr.ok is True
    types = [e.get("type") for e in events]
    assert types.count("host_task") >= 2
    assert events[0]["status"] == "running"
    assert events[-1]["status"] == "ok"
    assert "Debian" in (events[-1].get("host_label") or "")
