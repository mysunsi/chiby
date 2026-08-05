"""知识库自愈：防壳串台 / 只读探测不误修。"""

from __future__ import annotations

from unittest.mock import patch

from chibycore.closure_retry_runner import (
    diagnostic_closure_ok,
    is_readonly_diagnostic_command,
    kb_fix_relevant_to_command,
    run_closure_retry_loop,
)
from chibycore.closure_service import ClosurePayload
from chibycore.executor_contract import ExecResult, RiskLevel


def test_kb_relevance_rejects_adat_fix_for_nginx_status():
    ps = (
        "if (Test-Path -Path '.\\a.dat') { Remove-Item -Path '.\\a.dat' -Force } "
        "else { Write-Output 'File .\\a.dat does not exist, nothing to remove.' }"
    )
    assert not kb_fix_relevant_to_command("systemctl status nginx", ps)
    assert kb_fix_relevant_to_command(
        "rm a.dat",
        "if [ -e a.dat ]; then rm -f a.dat; else echo missing; fi",
    )


def test_systemctl_status_is_readonly_diagnostic():
    assert is_readonly_diagnostic_command("systemctl status nginx")
    assert is_readonly_diagnostic_command("systemctl is-active nginx")
    assert not is_readonly_diagnostic_command("systemctl restart nginx")


def test_diagnostic_ok_accepts_systemctl_exit_3():
    cp = ClosurePayload(
        trace_id="t",
        raw_command="systemctl status nginx",
        effective_command="systemctl status nginx",
        transport="ssh",
        risk_level=RiskLevel.LOW,
        exit_code=3,
        stdout="● nginx.service - nginx\n   Active: inactive (dead)\n",
        stderr="",
    )
    assert diagnostic_closure_ok("systemctl status nginx", cp)


def test_closure_status_exit3_no_kb_heal():
    """systemctl status exit=3 应视为诊断成功，不得套用知识库 PowerShell。"""
    ps = (
        "if (Test-Path -Path '.\\a.dat') { Remove-Item -Path '.\\a.dat' -Force } "
        "else { Write-Output 'File .\\a.dat does not exist, nothing to remove.' }"
    )
    tried: list[str] = []

    def execute(cmd: str) -> ExecResult:
        tried.append(cmd)
        if "systemctl" in cmd:
            return ExecResult(
                "● nginx.service\n   Active: inactive (dead)\n",
                "",
                3,
                "ssh",
                5,
                "t1",
                cmd,
            )
        return ExecResult("", "syntax error", 1, "ssh", 1, "t2", cmd)

    with patch(
        "chibycore.closure_retry_runner._lookup_kb_fixes",
        return_value=[ps],
    ):
        result = run_closure_retry_loop(
            trace_id="t-diag",
            initial_command="systemctl status nginx",
            execute=execute,
            gateway_allow=lambda c: (True, ""),
            shell_profile="unix",
            max_fix_attempts=2,
            success_mode="exit_code",
        )
    assert result.ok
    assert result.stop_reason == "success_initial"
    assert tried == ["systemctl status nginx"]
    assert result.fix_sources == []


def test_lookup_kb_filters_powershell_on_unix():
    from chibycore.closure_retry_runner import _lookup_kb_fixes

    class _Entry:
        remediation = (
            "if (Test-Path -Path '.\\a.dat') { Remove-Item -Path '.\\a.dat' -Force }"
        )

    class _R:
        score = 0.9
        entry_id = "e1"

    class _Resp:
        results = [_R()]

    class _Storage:
        def get_kb_entry(self, _id):
            return _Entry()

    class _Searcher:
        _storage = _Storage()

        def search(self, _q):
            return _Resp()

    cp = ClosurePayload(
        trace_id="t",
        raw_command="systemctl status nginx",
        effective_command="systemctl status nginx",
        transport="ssh",
        risk_level=RiskLevel.LOW,
        exit_code=3,
        stdout="inactive",
        stderr="",
    )
    with patch(
        "chibycore.closure_retry_runner._get_kb_search",
        return_value=_Searcher(),
    ):
        assert _lookup_kb_fixes([cp], shell_profile="unix") is None


def test_ops_conclusion_not_fooled_by_adat_does_not_exist():
    from terminal.mobile.orchestrator import _ops_user_conclusion

    out = (
        "bash: -c: line 0: syntax error near unexpected token `{'\n"
        "bash: -c: line 0: `if (Test-Path -Path '.\\a.dat') { Remove-Item "
        "-Path '.\\a.dat' -Force } else { Write-Output "
        "'File .\\a.dat does not exist, nothing to remove.' }'\n"
        "自愈来源：知识库命中"
    )
    conc = _ops_user_conclusion(
        command="systemctl status nginx",
        output=out,
        ok=False,
    )
    assert "没有名为 nginx" not in conc
    assert "经自愈" not in conc
    assert "未正确执行" in conc or "Windows" in conc
