import json

from chibycore.audit_log import reset_audit_log_for_tests
from chibycore.execution_gateway import ExecutionRequest, gateway_evaluate
from chibycore.metrics import get_gateway_metrics
from chibycore.policy_engine import reset_policy_engine_for_tests


def test_gateway_skip_policy_no_audit_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("OPS_POLICY_ENABLED", raising=False)
    monkeypatch.delenv("OPS_AUDIT_ALWAYS", raising=False)
    reset_policy_engine_for_tests()
    reset_audit_log_for_tests(None)
    p = tmp_path / "only.jsonl"
    monkeypatch.setenv("OPS_AUDIT_FILE", str(p))

    r = gateway_evaluate(
        ExecutionRequest(
            trace_id="t1",
            session_id="s1",
            command_line="echo hi",
            source="ws_exec",
            conn_type="ssh",
            host_id="h1",
        )
    )
    assert r.allowed is True
    assert not p.exists() or p.read_text().strip() == ""
    assert get_gateway_metrics().snapshot()["gateway_skip_policy"] >= 1


def test_gateway_deny_writes_audit(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_POLICY_ENABLED", "1")
    reset_policy_engine_for_tests()
    audit_path = tmp_path / "gate.jsonl"
    monkeypatch.setenv("OPS_AUDIT_FILE", str(audit_path))

    r = gateway_evaluate(
        ExecutionRequest(
            trace_id="t2",
            session_id="s2",
            command_line="rm -rf /",
            source="ws_plan",
            conn_type="winrm",
            host_id=None,
            plan_id="plt_x",
        )
    )
    assert r.allowed is False
    data = audit_path.read_text(encoding="utf-8").strip()
    assert "deny" in data
    rec = json.loads(data.splitlines()[-1])
    assert rec["decision"] == "deny"
    assert get_gateway_metrics().snapshot()["gateway_deny"] >= 1
    assert r.denial_category == "policy_deny"
    assert r.rule_kind == "deny_regex"
    assert r.matched_pattern and "rm" in r.matched_pattern.lower()


def test_gateway_change_window_hold_before_allow(monkeypatch, tmp_path):
    monkeypatch.delenv("OPS_POLICY_ENABLED", raising=False)
    reset_policy_engine_for_tests()
    reset_audit_log_for_tests(None)
    from chibycore.metrics import reset_metrics_for_tests

    reset_metrics_for_tests()
    monkeypatch.setenv("OPS_CHANGE_WINDOW_ENABLED", "1")
    audit_path = tmp_path / "cw.jsonl"
    monkeypatch.setenv("OPS_AUDIT_FILE", str(audit_path))

    from chibycore import execution_gateway as eg

    monkeypatch.setattr(eg, "is_change_window_frozen", lambda now=None: True)

    r = gateway_evaluate(
        ExecutionRequest(
            trace_id="tcw",
            session_id="s_cw",
            command_line="echo hi",
            source="ws_exec",
            conn_type="ssh",
            host_id="h1",
        )
    )
    assert r.allowed is False
    assert r.pending_change_control is True
    assert r.denial_category == "change_window_hold"
    assert r.override_requires_approval is True
    assert get_gateway_metrics().snapshot().get("gateway_change_window_hold", 0) >= 1
    data = audit_path.read_text(encoding="utf-8").strip()
    assert "hold_change_window" in data

    r2 = gateway_evaluate(
        ExecutionRequest(
            trace_id="tcw2",
            session_id="s_cw",
            command_line="echo hi",
            source="ws_exec",
            conn_type="ssh",
            host_id="h1",
            change_window_bypass=True,
        )
    )
    assert r2.allowed is True
    assert r2.pending_change_control is False
