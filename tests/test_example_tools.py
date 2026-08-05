"""example_echo Hello World 工具。"""
from __future__ import annotations

import pytest

from terminal.mobile.example_tools import (
    EXAMPLE_READONLY_TOOLS,
    EXAMPLE_TOOLS,
    format_example_result_summary,
    run_example_echo,
)
from terminal.mobile.remote_tools import (
    DEFAULT_ALLOWED_TOOLS,
    RemoteToolCall,
    call_needs_confirmation,
    execute_remote_tool_call,
    parse_remote_tool_calls,
)


def test_example_echo_in_whitelist():
    assert "example_echo" in DEFAULT_ALLOWED_TOOLS
    assert EXAMPLE_TOOLS == frozenset({"example_echo"})
    assert EXAMPLE_READONLY_TOOLS == frozenset({"example_echo"})


def test_run_example_echo():
    ok = run_example_echo(text="hello chiby")
    assert ok["ok"] is True
    assert ok["echo"] == "hello chiby"
    assert "hello chiby" in format_example_result_summary(ok)

    bad = run_example_echo(text="  ")
    assert bad["ok"] is False
    assert bad["error_code"] == "text_required"


def test_parse_and_no_confirm():
    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"example_echo","text":"ping"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text, allowed_tools=list(DEFAULT_ALLOWED_TOOLS))
    assert len(calls) == 1
    assert calls[0].tool == "example_echo"
    assert call_needs_confirmation(calls[0]) is False


@pytest.mark.asyncio
async def test_execute_example_echo_no_ssh():
    call = RemoteToolCall(
        tool="example_echo",
        raw={"tool": "example_echo", "text": "no-ssh"},
    )

    async def _boom(*_a, **_k):
        raise AssertionError("SSH must not run for example_echo")

    tr = await execute_remote_tool_call(
        call,
        executor=_boom,
        host_allowed=lambda _h: True,
        resolve_host=lambda _h: {},
    )
    assert tr.ok is True
    assert tr.error_code != "host_required"
    assert "no-ssh" in (tr.stdout or "")
