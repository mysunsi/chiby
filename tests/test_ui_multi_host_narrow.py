"""顶栏多选 + 窄问：应扇出；窄问不得判为模糊体检。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terminal.mobile.acl import AclUser, MobileAcl
from terminal.mobile.headless_exec import FakeHeadlessExecutor, adapt_command_for_conn
from terminal.mobile.hermes_protocol import (
    enrich_advanced_user_text,
    looks_like_narrow_ops_query,
    looks_like_vague_ops_check,
)
from terminal.mobile.job_targets import (
    infer_readonly_commands,
    intent_from_ui_host_ids,
    resolve_multi_host_scope,
)
from terminal.mobile.models import HostSummary, InboundMessage
from terminal.mobile.orchestrator import MobileSessionOrchestrator, call_ops_llm_plan_job


def _hosts() -> list[HostSummary]:
    return [
        HostSummary(id="linux-a", name="main.sunsi.cn", host="1.1.1.1", conn_type="ssh"),
        HostSummary(id="win-b", name="yl.sunsi.cn", host="2.2.2.2", conn_type="winrm"),
    ]


def test_infer_hostname_and_memory():
    n, cmds = infer_readonly_commands("当前主机名")
    assert "主机名" in n and cmds == ["hostname"]
    n2, cmds2 = infer_readonly_commands("内存还剩多少")
    assert cmds2 == ["free -h"]


def test_infer_top_memory_processes_not_free():
    n, cmds = infer_readonly_commands("哪些进程占用内存高")
    assert "进程" in n
    assert cmds and "ps aux" in cmds[0]
    assert "free" not in cmds[0]
    n2, cmds2 = infer_readonly_commands("看一下谁吃内存")
    assert cmds2 and "ps aux" in cmds2[0]


def test_resolve_multi_host_scope_ui_without_template():
    """顶栏多选 + 非模板问法：仍应给出多机范围（commands 可空）。"""
    scope = resolve_multi_host_scope(
        "帮我看看这几台有没有异常",
        _hosts(),
        ui_host_ids=["linux-a", "win-b"],
        allowed={"*"},
    )
    assert scope is not None
    assert set(scope.host_ids) == {"linux-a", "win-b"}
    assert scope.source == "ui_scope"


def test_intent_from_ui_host_ids():
    intent = intent_from_ui_host_ids(
        "当前主机名",
        ["linux-a", "win-b"],
        _hosts(),
        allowed={"*"},
    )
    assert intent is not None
    assert set(intent.host_ids) == {"linux-a", "win-b"}
    assert intent.commands == ["hostname"]
    assert intent.source == "ui_targets"


def test_hostname_not_vague_checkup():
    assert not looks_like_vague_ops_check("当前主机名")
    assert looks_like_narrow_ops_query("当前主机名")
    enriched = enrich_advanced_user_text(
        "当前主机名", host_id="linux-a", conn_type="ssh"
    )
    assert "窄问短答" in enriched
    assert "禁止全面体检" in enriched or "禁止" in enriched


def test_adapt_hostname_winrm():
    out = adapt_command_for_conn("winrm", "hostname")
    assert "COMPUTERNAME" in out or "GetHostName" in out
    out2 = adapt_command_for_conn("winrm", "free -h")
    assert "Win32_OperatingSystem" in out2
    out3 = adapt_command_for_conn("winrm", "ps aux --sort=-%mem | head -n 15")
    assert "Get-Process" in out3
    assert "WorkingSet64" in out3
    assert "MemoryMB" in out3


def test_call_ops_llm_plan_job_parses_ops_job():
    mock_llm = MagicMock()
    mock_llm.is_available = True
    mock_llm.chat.return_value = (
        "先查各机高占用进程。\n"
        "<<<OPS_JOB>>>\n"
        '{"name":"检测内存占用进程","host_ids":["linux-a","win-b"],'
        '"commands":["ps aux --sort=-%mem | head -n 15"],'
        '"readonly":true,"on_fail":"continue","max_parallel":5}\n'
        "<<<END_OPS_JOB>>>"
    )
    with patch("chibycore.llm_providers.get_llm", return_value=mock_llm):
        name, cmds, analysis = call_ops_llm_plan_job(
            user_question="哪些进程占用内存高",
            host_ids=["linux-a", "win-b"],
            suggested_name="检测内存",
            suggested_commands=["free -h"],
        )
    assert "进程" in name or name
    assert cmds and "ps aux" in cmds[0]
    assert "free" not in cmds[0]
    assert "高占用" in analysis or "进程" in analysis
    user_content = mock_llm.chat.call_args[0][0][1]["content"]
    assert "哪些进程占用内存高" in user_content
    assert "free -h" in user_content  # 规则建议出现在提示里供模型纠正


@pytest.mark.asyncio
async def test_advanced_ui_multi_fans_out_hostname():
    hosts = _hosts()
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
            auto_pick_single_host=False,
        ),
        executor=FakeHeadlessExecutor(),
        planner_mode="rules",
    )
    orch.set_ui_targets(
        conversation_id="c-multi",
        external_user_id="demo-user-1",
        host_ids=["linux-a", "win-b"],
    )
    await orch.set_agent_mode(
        conversation_id="c-multi",
        agent_mode="advanced",
        external_user_id="demo-user-1",
    )
    # 无 Hermes / LLM 时回退规则模板仍应扇出
    mock_llm = MagicMock()
    mock_llm.is_available = False
    with patch("chibycore.llm_providers.get_llm", return_value=mock_llm):
        reply = await orch.handle_message(
            InboundMessage(
                external_user_id="demo-user-1",
                conversation_id="c-multi",
                text="当前主机名",
            ),
        )
    assert reply.meta.get("kind") == "ops_job"
    assert set(reply.meta.get("host_ids") or []) == {"linux-a", "win-b"}
    assert "2 台" in reply.text or "2台" in reply.text.replace(" ", "")


@pytest.mark.asyncio
async def test_ops_multi_host_uses_llm_plan_not_rule_free():
    """运维多机：LLM 规划优先，纠正规则误绑的 free -h。"""
    hosts = _hosts()
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
            auto_pick_single_host=False,
        ),
        executor=FakeHeadlessExecutor(),
        planner_mode="rules",
    )
    orch.set_ui_targets(
        conversation_id="c-ops-mh",
        external_user_id="demo-user-1",
        host_ids=["linux-a", "win-b"],
    )
    await orch.set_agent_mode(
        conversation_id="c-ops-mh",
        agent_mode="ops",
        external_user_id="demo-user-1",
    )
    mock_llm = MagicMock()
    mock_llm.is_available = True
    mock_llm.chat.side_effect = [
        # 1) plan job
        (
            "查进程占用。\n"
            "<<<OPS_JOB>>>\n"
            '{"name":"检测内存占用进程","host_ids":["linux-a","win-b"],'
            '"commands":["ps aux --sort=-%mem | head -n 15"],'
            '"readonly":true}\n'
            "<<<END_OPS_JOB>>>"
        ),
        # 2) explain job（扇出后梳理）
        "**结论：两台均为进程内存排行。**\n\n| 主机 | Top |\n| --- | --- |\n| a | x |",
    ]
    with patch("chibycore.llm_providers.get_llm", return_value=mock_llm):
        reply = await orch.handle_message(
            InboundMessage(
                external_user_id="demo-user-1",
                conversation_id="c-ops-mh",
                text="哪些进程占用内存高",
            ),
        )
    assert reply.meta.get("kind") == "ops_job"
    assert reply.meta.get("source") == "ops_llm_job"
    assert "进程" in (reply.meta.get("name") or reply.text)
    assert "检测内存占用进程" in reply.text or "进程" in reply.text
    first_user = mock_llm.chat.call_args_list[0][0][0][1]["content"]
    assert "哪些进程占用内存高" in first_user
