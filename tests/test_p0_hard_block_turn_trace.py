"""P0：跨机硬拦截 + Turn Trace MVP。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from terminal.mobile.remote_tools import (
    RemoteToolCall,
    apply_ui_selected_host_authority,
    execute_remote_tool_call,
    host_selection_strict_enabled,
)
from terminal.mobile.turn_trace import (
    append_turn_trace,
    bind_turn_context,
    clear_turn_context,
    read_turn_trace,
)


def test_strict_reject_outside_selection():
    def resolve(hid: str):
        return SimpleNamespace(id=hid)

    targets, err = apply_ui_selected_host_authority(
        ["host-b"],
        selected_host_ids=["host-a"],
        resolve_host=resolve,
        tool="remote_run",
        strict=True,
    )
    assert targets == []
    assert "host_selection_violation" in err


@pytest.mark.asyncio
async def test_execute_rejects_wrong_host(monkeypatch):
    monkeypatch.setenv("OPS_HOST_SELECTION_STRICT", "1")
    assert host_selection_strict_enabled() is True

    class FakeExec:
        async def run(self, *a, **k):
            raise AssertionError("should not execute")

    call = RemoteToolCall(tool="ssh_execute", host="host-b", command="free -h")
    tr = await execute_remote_tool_call(
        call,
        executor=FakeExec(),
        host_allowed=lambda h: True,
        resolve_host=lambda h: SimpleNamespace(
            id=h, conn_type="ssh", password="x", username="u", host=h
        ),
        selected_host_ids=["host-a"],
        default_host_id="host-a",
        conn_type_for=lambda h: "ssh",
    )
    assert tr.ok is False
    assert tr.error_code == "host_selection_violation"


def test_turn_trace_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_TURN_TRACE", "1")
    monkeypatch.setenv("OPS_MOBILE_TURN_TRACE_DIR", str(tmp_path))
    bind_turn_context(turn_id="tur_test001", conversation_id="c1")
    try:
        append_turn_trace(
            "user_intent",
            payload={"text": "查内存", "bound_host_id": "h1"},
        )
        append_turn_trace(
            "tool_call",
            payload={"tool": "remote_run", "hosts": ["h1"]},
        )
        append_turn_trace(
            "tool_result",
            payload={"ok": True, "tool": "remote_run"},
            turn_id="tur_test001",
        )
    finally:
        clear_turn_context()
    rows = read_turn_trace("tur_test001")
    assert len(rows) == 3
    assert rows[0]["event"] == "user_intent"
    assert rows[1]["event"] == "tool_call"
    assert rows[2]["payload"]["ok"] is True
