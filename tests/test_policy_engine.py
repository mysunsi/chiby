from chibycore.policy_engine import PolicyEngine, policy_enabled, reset_policy_engine_for_tests


def test_policy_disabled_by_default(monkeypatch):
    monkeypatch.delenv("OPS_POLICY_ENABLED", raising=False)
    reset_policy_engine_for_tests()
    assert policy_enabled() is False
    eng = PolicyEngine()
    assert eng.evaluate_line("rm -rf /").allowed is True


def test_policy_deny_rm_rf_root(monkeypatch):
    monkeypatch.setenv("OPS_POLICY_ENABLED", "1")
    reset_policy_engine_for_tests()
    eng = PolicyEngine()
    r = eng.evaluate_line("sudo rm -rf /")
    assert r.allowed is False
    assert "拒绝" in r.reason or "rm" in r.reason.lower()


def test_policy_allow_ls(monkeypatch):
    monkeypatch.setenv("OPS_POLICY_ENABLED", "1")
    reset_policy_engine_for_tests()
    eng = PolicyEngine()
    assert eng.evaluate_line("ls -la /tmp").allowed is True
