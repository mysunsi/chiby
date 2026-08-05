"""自愈来源可观测：fix pipeline / humanize。"""

from __future__ import annotations

from unittest.mock import patch

from chibycore.executor_contract import ExecResult, RiskLevel
from chibycore.closure_service import ClosurePayload


def _cp(cmd: str = "false", exit_code: int = 1) -> ClosurePayload:
    return ClosurePayload(
        trace_id="t1",
        raw_command=cmd,
        effective_command=cmd,
        transport="local",
        risk_level=RiskLevel.LOW,
        exit_code=exit_code,
        stdout="",
        stderr="permission denied",
    )


def test_call_fix_pipeline_with_source_remediator():
    from chibycore.closure_llm_fix import call_fix_pipeline_with_source

    with patch(
        "chibycore.remediator_fix_bridge.remediator_fix_enabled",
        return_value=True,
    ), patch(
        "chibycore.remediator_fix_bridge.call_remediator_for_fix_commands",
        return_value=["sudo false"],
    ):
        fixes, src = call_fix_pipeline_with_source([_cp()], shell_profile="unix")
    assert fixes == ["sudo false"]
    assert src == "remediator"


def test_call_fix_pipeline_with_source_falls_back_llm():
    from chibycore.closure_llm_fix import call_fix_pipeline_with_source

    with patch(
        "chibycore.remediator_fix_bridge.remediator_fix_enabled",
        return_value=True,
    ), patch(
        "chibycore.remediator_fix_bridge.call_remediator_for_fix_commands",
        return_value=[],
    ), patch(
        "chibycore.closure_llm_fix.call_llm_for_fix_commands",
        return_value=["echo ok"],
    ):
        fixes, src = call_fix_pipeline_with_source([_cp()], shell_profile="unix")
    assert fixes == ["echo ok"]
    assert src == "llm"


def test_humanize_keeps_heal_source():
    from terminal.mobile.orchestrator import _humanize_exec_body, _ops_user_conclusion

    raw = (
        "[Agent闭环·成功] host=`h1` fix_rounds=1\n"
        "$ false\n"
        "结束原因：success_after_fix\n"
        "自愈来源：remediator（结构化修复）\n"
        "exit=0\n"
        "ok\n"
    )
    human = _humanize_exec_body(raw, command="false")
    assert "自愈来源：remediator" in human
    assert "Agent闭环" not in human
    conc = _ops_user_conclusion(command="false", output=human, ok=True)
    assert "自愈" in conc or "remediator" in conc


def test_closure_result_records_fix_source():
    from chibycore.closure_retry_runner import run_closure_retry_loop

    def execute(cmd: str) -> ExecResult:
        if cmd.startswith("sudo"):
            return ExecResult("fixed", "", 0, "ssh", 1, "t2", cmd)
        return ExecResult("", "permission denied", 1, "ssh", 1, "t1", cmd)

    with patch(
        "chibycore.closure_llm_fix.call_fix_pipeline_with_source",
        return_value=(["sudo false"], "remediator"),
    ), patch(
        "chibycore.closure_retry_runner._lookup_kb_fixes",
        return_value=None,
    ):
        result = run_closure_retry_loop(
            trace_id="t-heal",
            initial_command="false",
            execute=execute,
            gateway_allow=lambda c: (True, ""),
            max_fix_attempts=1,
            success_mode="exit_code",
        )
    assert result.ok
    assert result.stop_reason == "success_after_fix"
    assert "remediator" in result.fix_sources
