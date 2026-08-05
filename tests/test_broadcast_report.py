"""群发汇报口吻设置与规则汇总。"""
from __future__ import annotations

from chibyterm.broadcast_report import (
    BroadcastHostResult,
    comparative_report_md,
    rule_comparative_report,
    system_prompt_for_tone,
)
from chibyterm.broadcast_settings import (
    normalize_report_tone,
    save_broadcast_settings,
)


def _hosts():
    return [
        BroadcastHostResult(
            session_id="a",
            host_label="host-a",
            status="pass",
            ok=True,
            explain_md="**结论：内存正常。**",
            stdout_tail="Mem: 8G",
        ),
        BroadcastHostResult(
            session_id="b",
            host_label="host-b",
            status="fail",
            ok=False,
            explain_md="**结论：命令失败。**",
            error="timeout",
        ),
    ]


def test_normalize_report_tone_aliases():
    assert normalize_report_tone("risk") == "risk"
    assert normalize_report_tone("compliance") == "risk"
    assert normalize_report_tone("ceo") == "strategy"
    assert normalize_report_tone("nope") == "ops"


def test_system_prompt_risk_mentions_rating():
    text = system_prompt_for_tone("risk", ui_locale="zh-CN")
    assert "总体风险评级" in text
    assert "P0" in text


def test_rule_comparative_report_risk_zh():
    md = rule_comparative_report(
        command="free -h",
        results=_hosts(),
        ui_locale="zh-CN",
        report_tone="risk",
    )
    assert "总体风险评级" in md
    assert "整改优先级" in md
    assert "host-b" in md


def test_rule_comparative_report_strategy_short():
    md = rule_comparative_report(
        command="free -h",
        results=_hosts(),
        ui_locale="zh-CN",
        report_tone="strategy",
    )
    assert "一句话结论" in md
    assert "年度建议" in md


def test_rule_comparative_report_ops_zh():
    md = rule_comparative_report(command="free -h", results=_hosts(), ui_locale="zh-CN")
    assert "总体结论" in md
    assert "关键指标对比" in md
    assert md.index("host-b") < md.index("host-a")


def test_comparative_report_falls_back_without_llm(monkeypatch):
    from chibycore import llm_providers as lp

    class _Empty:
        is_available = False

    monkeypatch.setattr(lp, "get_llm", lambda: _Empty())
    md = comparative_report_md(
        command="uname -a",
        results=_hosts(),
        ui_locale="zh-CN",
        report_tone="capacity",
    )
    assert "总体水位评估" in md or "需补充历史监控数据" in md


def test_save_broadcast_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chibyterm.broadcast_settings._settings_path",
        lambda: tmp_path / "broadcast_settings.json",
    )
    out = save_broadcast_settings({"report_tone": "strategy"})
    assert out["report_tone"] == "strategy"
    from chibyterm.broadcast_settings import load_broadcast_settings

    assert load_broadcast_settings()["report_tone"] == "strategy"
