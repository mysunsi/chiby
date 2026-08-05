"""工业级模块单测：隔离环境变量与单例。"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def industrial_isolation(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("OPS_AUDIT_FILE", str(audit))
    # 掌上会话持久化隔离到临时目录，避免污染 data/mobile_sessions 或串测
    sess = tmp_path / "mobile_sessions"
    sess.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPS_MOBILE_SESSION_DIR", str(sess))
    monkeypatch.delenv("OPS_POLICY_EXTRA_DENY", raising=False)
    monkeypatch.delenv("OPS_AUDIT_ALWAYS", raising=False)

    from chibycore import audit_log as al
    from chibycore import metrics as met
    from chibycore import policy_engine as pe
    from chibycore.risk_heuristic import reset_risk_keyword_cache_for_tests

    reset_risk_keyword_cache_for_tests()
    pe.reset_policy_engine_for_tests()
    met.reset_metrics_for_tests()
    al.reset_audit_log_for_tests(None)
    yield
    reset_risk_keyword_cache_for_tests()
    pe.reset_policy_engine_for_tests()
    met.reset_metrics_for_tests()
    al.reset_audit_log_for_tests(None)
