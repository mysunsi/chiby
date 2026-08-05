"""意图清单：拆分、逐项闭环、进度状态。"""
from __future__ import annotations

from chibycore.execution_gateway import GatewayAllowResult
from chibycore.executor_contract import ExecResult
from chibycore.intent_checklist import (
    checklist_from_plan_steps,
    maybe_split_compound_command,
    run_intent_checklist,
)


def test_maybe_split_compound_nginx():
    cmd = (
        'nginx -t && echo "--- 配置路径 ---" && '
        "nginx -V 2>&1 | grep configure && echo --- && nginx -T"
    )
    parts = maybe_split_compound_command(cmd)
    assert len(parts) >= 3
    assert any("nginx -t" in p for p in parts)
    assert any("nginx -T" in p for p in parts)


def test_checklist_from_single_compound_step():
    intent = "查 nginx 配置信息"
    steps = [{"title": "查询", "command": "nginx -t && nginx -V && nginx -T"}]
    cl = checklist_from_plan_steps(intent, steps)
    assert cl.total == 3
    assert cl.items[0].description == "检查 nginx 配置语法"
    assert cl.items[-1].description == "打印 nginx 完整配置"


def test_checklist_from_multi_steps_no_extra_split_needed():
    steps = [
        {"title": "语法", "command": "nginx -t"},
        {"title": "完整", "command": "nginx -T"},
    ]
    cl = checklist_from_plan_steps("查配置", steps)
    assert cl.total == 2
    assert cl.items[0].description == "语法"


def test_run_intent_checklist_nginx_flow():
    init_parts = {
        "nginx -t": "perm",
        "nginx -V": "ok",
        "nginx -T": "ok",
    }
    # After sudo elevates -t
    calls = []

    def ex(cmd: str) -> ExecResult:
        calls.append(cmd)
        c = cmd.strip()
        if c == "nginx -t":
            return ExecResult(
                "",
                'open() "/run/nginx.pid" failed (13: Permission denied)',
                1,
                "ssh",
                1,
                "t",
                c,
            )
        if c.startswith("sudo") and "nginx -t" in c and "nginx -T" not in c and "nginx -V" not in c:
            return ExecResult("", "test is successful", 0, "ssh", 1, "t1", c)
        if "nginx -V" in c:
            return ExecResult("configure arguments: --prefix=/etc/nginx", "", 0, "ssh", 1, "t2", c)
        if "nginx -T" in c:
            return ExecResult("http { server {} }\n", "", 0, "ssh", 1, "t3", c)
        return ExecResult("", "unexpected " + c, 1, "ssh", 1, "tx", c)

    cl = checklist_from_plan_steps(
        "查 nginx 配置信息",
        [{"command": "nginx -t && nginx -V && nginx -T"}],
    )
    # Force heuristic sudo on first item: enable fallback
    import os

    os.environ["OPS_CLOSURE_FIX_FALLBACK"] = "1"
    os.environ["OPS_CLOSURE_REMEDIATOR_FIX"] = "0"

    from unittest.mock import patch

    with patch("chibycore.closure_retry_runner._lookup_kb_fixes", lambda *a, **k: []):
        with patch(
            "chibycore.closure_llm_fix.call_llm_for_fix_commands",
            lambda *a, **k: [],
        ):
            # First item only needs sudo nginx -t via heuristic; goal_resume may re-run elevated
            run_intent_checklist(
                checklist=cl,
                execute=ex,
                gateway_allow=lambda c: GatewayAllowResult(True, "", False),
                shell_profile="unix",
                max_fix_attempts=2,
                success_mode="exit_code",
            )

    assert cl.status == "completed"
    assert cl.completed_count == 3
    assert any("nginx -T" in c for c in calls)


def test_run_intent_checklist_partial_on_failure():
    def ex(cmd: str) -> ExecResult:
        if "good" in cmd:
            return ExecResult("ok", "", 0, "ssh", 1, "t", cmd)
        return ExecResult("", "boom", 1, "ssh", 1, "t", cmd)

    cl = checklist_from_plan_steps(
        "两步",
        [
            {"command": "echo good"},
            {"command": "echo bad"},
        ],
        split_compound=False,
    )
    run_intent_checklist(
        checklist=cl,
        execute=ex,
        gateway_allow=lambda c: GatewayAllowResult(True, "", False),
        max_fix_attempts=0,
        success_mode="exit_code",
        stop_on_item_failure=True,
    )
    assert cl.items[0].status == "completed"
    assert cl.items[1].status == "failed"
    assert cl.status in ("partial", "failed")
