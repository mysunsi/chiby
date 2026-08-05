"""Phase 3：闭环重试 + LLM JSON 解析 + fallback。"""
from __future__ import annotations

from unittest.mock import MagicMock

from chibycore.closure_llm_fix import parse_fix_commands_json
from chibycore.closure_retry_runner import (
    build_goal_resume_command,
    evaluate_closure_success,
    fix_covers_original_goal,
    run_closure_retry_loop,
)
from chibycore.closure_service import build_closure_payload
from chibycore.execution_gateway import GatewayAllowResult
from chibycore.executor_contract import ExecResult


def test_parse_fix_commands_json_raw():
    s = '{"commands":["echo 1","echo 2"]}'
    assert parse_fix_commands_json(s) == ["echo 1", "echo 2"]


def test_parse_fix_commands_json_fenced():
    s = 'Here:\n```json\n{"commands":["x"]}\n```'
    assert parse_fix_commands_json(s) == ["x"]


def test_fix_covers_and_goal_resume_helpers():
    import shlex

    init = 'nginx -t && echo "---" && nginx -T'
    assert fix_covers_original_goal("sudo nginx -t", init) is False
    assert fix_covers_original_goal("sudo " + init, init) is True
    assert fix_covers_original_goal("sudo bash -lc " + shlex.quote(init), init) is True
    hist = [
        build_closure_payload(
            trace_id="t",
            raw_command=init,
            effective_command=init,
            result=ExecResult(
                "",
                'open() "/run/nginx.pid" failed (13: Permission denied)',
                1,
                "ssh",
                1,
                "t",
                init,
            ),
        )
    ]
    resume = build_goal_resume_command(init, "sudo nginx -t", hist, shell_profile="unix")
    assert resume is not None
    assert "nginx -T" in resume
    assert resume.startswith("sudo")
    # 整命令替换：不要求复跑
    assert build_goal_resume_command("failcmd", "goodcmd", [], shell_profile="unix") is None


def test_evaluate_closure_success_both_mock_judge():
    er = ExecResult("x", "", 0, "ssh", 1, "t", "cmd")
    cp = build_closure_payload(
        trace_id="t",
        raw_command="cmd",
        effective_command="cmd",
        result=er,
    )
    ok, _ = evaluate_closure_success(
        cp,
        success_mode="both",
        success_exit_codes=[0],
        llm_judge_fn=lambda _: (True, "ok"),
    )
    assert ok is True
    ok2, _ = evaluate_closure_success(
        cp,
        success_mode="both",
        success_exit_codes=[0],
        llm_judge_fn=lambda _: (False, "bad"),
    )
    assert ok2 is False


def test_closure_retry_success_initial():
    calls = []

    def ex(cmd: str) -> ExecResult:
        calls.append(cmd)
        return ExecResult(
            stdout="",
            stderr="",
            exit_code=0,
            transport="ssh",
            duration_ms=1,
            trace_id="t",
            command=cmd,
        )

    def gw(cmd: str):
        return GatewayAllowResult(True, "", False)

    ok_cb = MagicMock()
    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command="true",
        execute=ex,
        gateway_allow=gw,
        on_success=ok_cb,
    )
    assert r.ok is True
    assert r.stop_reason == "success_initial"
    ok_cb.assert_called_once()
    assert calls == ["true"]


def test_closure_retry_fallback_sudo(monkeypatch):
    # 只测启发式 sudo：关掉 remediator / KB / LLM，避免本机配置抢走候选
    monkeypatch.setenv("OPS_CLOSURE_FIX_FALLBACK", "1")
    monkeypatch.setenv("OPS_CLOSURE_REMEDIATOR_FIX", "0")
    monkeypatch.setattr(
        "chibycore.closure_retry_runner._lookup_kb_fixes",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "chibycore.closure_llm_fix.call_llm_for_fix_commands",
        lambda *a, **k: [],
    )
    n = 0

    def ex(cmd: str) -> ExecResult:
        nonlocal n
        n += 1
        if n == 1:
            return ExecResult(
                stdout="",
                stderr="permission denied",
                exit_code=1,
                transport="ssh",
                duration_ms=1,
                trace_id="t1",
                command=cmd,
            )
        return ExecResult(
            stdout="ok",
            stderr="",
            exit_code=0,
            transport="ssh",
            duration_ms=1,
            trace_id="t2",
            command=cmd,
        )

    def gw(cmd: str):
        return GatewayAllowResult(True, "", False)

    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command="touch /root/x",
        execute=ex,
        gateway_allow=gw,
    )
    assert r.ok is True
    assert r.stop_reason == "success_after_fix"
    assert "sudo" in (r.final_payload.effective_command if r.final_payload else "")


def test_closure_initial_change_control_hold():
    def ex(_: str) -> ExecResult:
        raise AssertionError("should not execute")

    def gw(_: str):
        return GatewayAllowResult(False, "freeze", True, "pc_test")

    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command="systemctl restart nginx",
        execute=ex,
        gateway_allow=gw,
    )
    assert r.ok is False
    assert r.stop_reason == "initial_change_control_hold"
    assert r.steps[0].pending_change_control is True
    assert r.steps[0].change_control_pending_id == "pc_test"


def test_closure_initial_gateway_denied():
    def ex(_: str) -> ExecResult:
        raise AssertionError("should not execute")

    def gw(_: str):
        return GatewayAllowResult(False, "blocked", False)

    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command="rm -rf /",
        execute=ex,
        gateway_allow=gw,
    )
    assert r.ok is False
    assert r.stop_reason == "initial_gateway_denied"


def test_closure_injected_llm_fix():
    calls = []

    def ex2(cmd: str) -> ExecResult:
        calls.append(cmd)
        if cmd == "failcmd":
            return ExecResult("", "e", 1, "ssh", 1, "t", cmd)
        return ExecResult("y", "", 0, "ssh", 1, "t2", cmd)

    def llm_hist(h):
        if len(h) == 1:
            return ["goodcmd"]
        return []

    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command="failcmd",
        execute=ex2,
        gateway_allow=lambda c: GatewayAllowResult(True, "", False),
        llm_fix_commands=llm_hist,
        max_fix_attempts=2,
    )
    assert r.ok is True
    assert "goodcmd" in calls


def test_closure_goal_resume_after_partial_nginx_fix():
    """权限失败后若只修好 nginx -t，须续跑提权后的完整原命令。"""
    init = "nginx -t && echo --- && nginx -T"
    calls = []

    def ex(cmd: str) -> ExecResult:
        calls.append(cmd)
        if cmd == init:
            return ExecResult(
                "",
                'nginx: [emerg] open() "/run/nginx.pid" failed (13: Permission denied)',
                1,
                "ssh",
                1,
                "t0",
                cmd,
            )
        if cmd.strip() == "sudo nginx -t":
            return ExecResult(
                "",
                "nginx: the configuration file /etc/nginx/nginx.conf test is successful",
                0,
                "ssh",
                1,
                "t1",
                cmd,
            )
        if "nginx -T" in cmd and cmd.strip().startswith("sudo"):
            return ExecResult(
                "http { server { listen 80; } }\n",
                "",
                0,
                "ssh",
                1,
                "t2",
                cmd,
            )
        return ExecResult("", "unexpected:" + cmd, 1, "ssh", 1, "tx", cmd)

    def llm_hist(h):
        # 仅在首轮失败后给出「缩水」修复
        if len(h) == 1:
            return ["sudo nginx -t"]
        return []

    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command=init,
        execute=ex,
        gateway_allow=lambda c: GatewayAllowResult(True, "", False),
        llm_fix_commands=llm_hist,
        max_fix_attempts=2,
        success_mode="exit_code",
    )
    assert r.ok is True
    assert r.stop_reason == "success_after_fix"
    assert any(s.phase == "goal_resume" for s in r.steps)
    assert any("nginx -T" in c and c.startswith("sudo") for c in calls)
    assert r.final_payload and "http {" in (r.final_payload.stdout or "")


def test_closure_repair_ok_goal_unverified_when_resume_fails():
    init = "nginx -t && nginx -T"
    calls = []

    def ex(cmd: str) -> ExecResult:
        calls.append(cmd)
        if "Permission" in cmd or cmd == init:
            return ExecResult("", "Permission denied on pid", 1, "ssh", 1, "t", cmd)
        if cmd.strip() == "sudo nginx -t":
            return ExecResult("", "test is successful", 0, "ssh", 1, "t1", cmd)
        # 复验原目标仍失败
        return ExecResult("", "still denied", 1, "ssh", 1, "t2", cmd)

    r = run_closure_retry_loop(
        trace_id="tr",
        initial_command=init,
        execute=ex,
        gateway_allow=lambda c: GatewayAllowResult(True, "", False),
        llm_fix_commands=lambda h: ["sudo nginx -t"] if len(h) == 1 else [],
        max_fix_attempts=1,
        success_mode="exit_code",
    )
    assert r.ok is False
    assert r.stop_reason == "repair_ok_goal_unverified"
    assert any(s.phase == "goal_resume" for s in r.steps)
