# -*- coding: utf-8 -*-
"""产品层斜杠命令拦截。"""

from __future__ import annotations

import pytest

from terminal.mobile.slash_commands import (
    classify_slash,
    denied_slash_text,
    language_cli_guard_line,
    parse_slash_command,
    product_help_text,
)


def test_parse_reset_and_model():
    r = parse_slash_command("/reset")
    assert r is not None and r.name == "reset"
    assert classify_slash(r) == "allow"

    m = parse_slash_command("/model")
    assert m is not None and m.name == "model"
    assert classify_slash(m) == "deny"

    u = parse_slash_command("/foobar")
    assert u is not None
    assert classify_slash(u) == "unknown"


def test_parse_ignores_paths_and_prose():
    assert parse_slash_command("/var/log/nginx") is None
    assert parse_slash_command("请执行 /reset 一下") is None
    assert parse_slash_command("df -h") is None
    assert parse_slash_command("/model deepseek\n再查内存") is None


def test_help_and_deny_copy():
    help_t = product_help_text()
    assert "Chiby" in help_t
    assert "Hermes" not in help_t
    assert "/model" not in help_t
    assert "自然语言" in help_t
    m = parse_slash_command("/yolo")
    assert m is not None
    deny = denied_slash_text(m)
    assert "不支持" in deny
    assert "Hermes" not in deny
    assert "斜杠命令铁律" in language_cli_guard_line()


@pytest.mark.asyncio
async def test_orchestrator_blocks_model_slash():
    from terminal.mobile.models import InboundMessage
    from terminal.mobile.orchestrator import MobileSessionOrchestrator

    class _Hosts:
        def list_hosts(self):
            return []

    orch = MobileSessionOrchestrator(
        host_provider=_Hosts(),  # type: ignore[arg-type]
        planner_mode="rules",
    )
    # bypass ACL：直接测 slash 辅助方法
    from terminal.mobile.orchestrator import ConversationState

    st = ConversationState(conversation_id="slash-test-1", agent_mode="omnipotent")
    orch._conversations["slash-test-1"] = st
    msg = InboundMessage(
        conversation_id="slash-test-1",
        external_user_id="u1",
        text="/model",
        channel="demo",
    )
    reply = await orch._maybe_handle_slash_command(
        msg, st=st, raw_text="/model"
    )
    assert reply is not None
    assert reply.meta and reply.meta.get("kind") == "slash_deny"
    assert "不支持" in (reply.text or "")


@pytest.mark.asyncio
async def test_orchestrator_reset_clears_checkpoint():
    from terminal.mobile.models import InboundMessage
    from terminal.mobile.orchestrator import ConversationState, MobileSessionOrchestrator

    class _Hosts:
        def list_hosts(self):
            return []

    orch = MobileSessionOrchestrator(
        host_provider=_Hosts(),  # type: ignore[arg-type]
        planner_mode="rules",
    )
    st = ConversationState(
        conversation_id="slash-test-2",
        agent_mode="omnipotent",
        bound_host_id="h1",
        diag_focus=["nginx 报错"],
        a2_closure_resume={"host_id": "h1", "rounds_done": 2},
        last_user_text="查内存",
        awaiting_followup=True,
    )
    orch._conversations["slash-test-2"] = st
    msg = InboundMessage(
        conversation_id="slash-test-2",
        external_user_id="u1",
        text="/reset",
        channel="demo",
    )
    reply = await orch._maybe_handle_slash_command(
        msg, st=st, raw_text="/reset"
    )
    assert reply is not None
    assert reply.meta and reply.meta.get("kind") == "slash_reset"
    assert st.bound_host_id == "h1"
    assert st.diag_focus == []
    assert st.a2_closure_resume is None
    assert st.last_user_text == ""
    assert st.awaiting_followup is False
    assert "已重置" in (reply.text or "")
