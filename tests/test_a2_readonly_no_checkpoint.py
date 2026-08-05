"""全能 A2：纯只读探索不弹「继续」检查点。"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from terminal.hermes_bridge.acp_worker import WorkerTurnResult
from terminal.mobile.acl import AclUser, MobileAcl
from terminal.mobile.headless_exec import FakeHeadlessExecutor
from terminal.mobile.models import HostSummary, InboundMessage
from terminal.mobile.orchestrator import (
    MobileSessionOrchestrator,
    _a2_calls_pure_readonly,
    _a2_rows_pure_readonly,
    _a2_tool_is_pure_readonly,
)
from terminal.mobile.remote_tools import RemoteToolCall, RemoteToolResult


def test_a2_readonly_helpers():
    assert _a2_tool_is_pure_readonly("remote_list_dir")
    assert _a2_tool_is_pure_readonly("remote_read_file")
    assert _a2_tool_is_pure_readonly("host_list")
    assert not _a2_tool_is_pure_readonly("remote_write_file")
    assert not _a2_tool_is_pure_readonly("remote_mkdir")
    assert not _a2_tool_is_pure_readonly("ssh_execute")

    rows = [
        RemoteToolResult(
            tool="remote_read_file",
            ok=True,
            host="h1",
            command="remote_read_file",
            stdout="x",
        ),
        RemoteToolResult(
            tool="remote_list_dir",
            ok=True,
            host="h1",
            command="remote_list_dir",
            stdout="y",
        ),
    ]
    assert _a2_rows_pure_readonly(rows) is True
    rows2 = rows + [
        RemoteToolResult(
            tool="ssh_execute",
            ok=True,
            host="h1",
            command="uptime",
            stdout="1",
        )
    ]
    assert _a2_rows_pure_readonly(rows2) is False

    calls = [
        RemoteToolCall(tool="remote_read_file", host="h1", path=r"C:\a.py"),
        RemoteToolCall(tool="remote_list_dir", host="h1", path=r"C:\Open"),
    ]
    assert _a2_calls_pure_readonly(calls) is True


def _read_block(host: str, path: str) -> str:
    return (
        "<<<REMOTE_TOOL>>>\n"
        f'{{"tool":"remote_read_file","host":"{host}","path":"{path}"}}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )


class _ReadonlyLoopPlanner:
    """连续多轮只吐 remote_read_file，模拟大量读代码。"""

    def __init__(self, rounds: int = 6) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.available = True
        self.init_error = ""
        self.rounds = rounds

    async def begin_turn(self, **kwargs: Any) -> Dict[str, Any]:
        self.calls.append(kwargs)
        n = len(self.calls)
        if n <= self.rounds:
            body = (
                f"继续读第 {n} 个文件。\n"
                + _read_block("h1", f"C:/Open/Api/src/f{n}.py")
            )
        else:
            body = "结论：代码结构清晰，主要是 FastAPI 服务。"
        return {
            "status": "done",
            "agent_mode": "omnipotent",
            "result": WorkerTurnResult(assistant_text=body, exec_hints=[]),
        }


@pytest.mark.asyncio
async def test_omnipotent_readonly_skips_ask_every_checkpoint(monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_A2_LOOP_ASK_EVERY", "2")
    monkeypatch.setenv("OPS_MOBILE_A2_LOOP_CAP", "8")
    monkeypatch.setattr(
        "terminal.mobile.cmd_extract.extract_commands_via_llm",
        lambda *a, **k: [],
    )

    async def _fake_exec(call, **kwargs):
        return RemoteToolResult(
            tool=call.tool,
            ok=True,
            host=call.host or "h1",
            command=f"{call.tool} {call.path}",
            stdout="file-body\n",
            exit_code=0,
            duration_ms=1,
        )

    monkeypatch.setattr(
        "terminal.mobile.remote_tools.execute_remote_tool_call",
        _fake_exec,
    )

    hosts = [HostSummary(id="h1", name="D", host="1.1.1.1", conn_type="winrm")]
    planner = _ReadonlyLoopPlanner(rounds=5)
    orch = MobileSessionOrchestrator(
        host_provider=lambda: hosts,
        acl=MobileAcl(
            users={
                "demo-user-1": AclUser(
                    external_user_id="demo-user-1",
                    internal_user="ops",
                    host_ids={"*"},
                ),
            },
        ),
        executor=FakeHeadlessExecutor(),
        hermes_planner=planner,
        planner_mode="auto",
    )
    st = orch._state("c-ro")
    st.bound_host_id = "h1"
    st.agent_mode = "omnipotent"
    st.last_user_text = "看看 C:\\Open\\Api 下的代码"

    r = await orch.handle_message(
        InboundMessage(
            external_user_id="demo-user-1",
            conversation_id="c-ro",
            text="看看 C:\\Open\\Api 下的代码",
        ),
    )
    assert r.meta.get("kind") != "a2_continue_confirm", r.meta
    assert len(planner.calls) >= 4
    assert "结论" in (r.text or "") or r.meta.get("kind") == "remote_tools_closed_loop"
