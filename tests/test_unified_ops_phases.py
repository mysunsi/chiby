"""统一执行器 + 规则 + 闭环 + 学习骨架（Phase 0～4 单测）。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from chibycore.closure_service import (
    RetryBudget,
    build_closure_payload,
    success_for_closure,
)
from chibycore.command_soft_delete import soften_linux_command, suggest_win_recycle
from chibycore.executor_contract import ExecResult, RiskLevel
from chibycore.learning_stub import propose_rule_hints, summarize_audit_denies
from chibycore.os_risk_loader import load_os_rules_file
from chibycore.policy_engine import PolicyEngine, policy_enabled, reset_policy_engine_for_tests
from chibycore.risk_heuristic import heuristic_risk_level, reset_risk_keyword_cache_for_tests
from chibycore.ssh_oneshot import ParamikoSSHOneShotExecutor


def test_load_os_rules_file_default(tmp_path):
    patterns, kw = load_os_rules_file()
    assert isinstance(patterns, list)
    assert "critical" in kw or isinstance(kw, dict)


def test_heuristic_risk_level():
    reset_risk_keyword_cache_for_tests()
    assert heuristic_risk_level("rm -rf /tmp", {"critical": ["rm -rf /"], "high": []}) == RiskLevel.CRITICAL
    assert heuristic_risk_level("echo ok", {"critical": [], "high": []}) == RiskLevel.LOW


def test_closure_build_and_success():
    er = ExecResult(
        stdout="ok",
        stderr="",
        exit_code=0,
        transport="ssh",
        duration_ms=1,
        trace_id="t1",
        command="echo ok",
    )
    cp = build_closure_payload(
        trace_id="t1",
        raw_command="echo ok",
        effective_command="echo ok",
        result=er,
    )
    assert cp.exit_code == 0
    assert success_for_closure(cp) is True
    assert cp.risk_level == RiskLevel.LOW


def test_retry_budget():
    b = RetryBudget(max_attempts=3)
    assert b.can_retry()
    b.consume()
    b.consume()
    b.consume()
    assert not b.can_retry()


def test_soft_delete_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OPS_SOFT_DELETE_LINUX", raising=False)
    o = soften_linux_command("rm -rf /tmp/x")
    assert o.applied is False


def test_soft_delete_enabled(monkeypatch):
    monkeypatch.setenv("OPS_SOFT_DELETE_LINUX", "1")
    o = soften_linux_command("rm -rf /tmp/x")
    assert o.applied is True
    assert "trash-put" in o.rewritten


def test_suggest_win_recycle():
    orig, ps = suggest_win_recycle("Remove-Item -Recurse x")
    assert orig.startswith("Remove-Item")
    assert "Move-Item" in ps


def test_learning_summarize(tmp_path):
    p = tmp_path / "a.jsonl"
    rows = [
        {"event": "execution_gateway", "decision": "deny", "reason": "bad"},
    ] * 3
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    s = summarize_audit_denies(p)
    assert s["sample"] >= 2
    hints = propose_rule_hints(s)
    assert len(hints) >= 1


def test_policy_merges_yaml_extra(tmp_path, monkeypatch):
    y = tmp_path / "r.yaml"
    y.write_text(
        "extra_deny_patterns:\n  - '.*FORBIDDEN_UNIQUE_XYZ123.*'\nrisk_keywords:\n  critical: []\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_POLICY_OS_RULES_FILE", str(y))
    monkeypatch.setenv("OPS_POLICY_ENABLED", "1")
    reset_policy_engine_for_tests()
    pe = PolicyEngine()
    pr = pe.evaluate_line("echo FORBIDDEN_UNIQUE_XYZ123")
    assert pr.allowed is False


def test_ssh_oneshot_run_command_mock():
    ex = ParamikoSSHOneShotExecutor("h", 22, "u", password="p")
    mock_client = MagicMock()
    mock_stdin = MagicMock()
    mock_stdout = MagicMock()
    mock_stderr = MagicMock()
    # pump_raw 循环读到空字节才结束；恒定 return_value 会读到超时 exit_code=-1
    mock_stdout.read.side_effect = [b"out\n", b""]
    mock_stderr.read.side_effect = [b""]
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_client.exec_command.return_value = (mock_stdin, mock_stdout, mock_stderr)
    with patch.object(ex, "_client", mock_client):
        r = ex.run_command("whoami")
    assert r.exit_code == 0
    assert "out" in r.stdout
