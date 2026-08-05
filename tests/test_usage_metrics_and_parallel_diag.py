"""选项1：匿名指标 + 诊断工具并行分组。"""
from __future__ import annotations

import json
from pathlib import Path

from chibycore.platform_audit import append_platform_audit, reset_platform_audit_for_tests
from chibycore.usage_metrics import collect_anonymous_metrics, refresh_usage_metrics
from chibyterm.multihost_diag import DIAG_TOOLS, diag_command


def test_diag_tools_are_independent():
    assert "process_list" in DIAG_TOOLS
    assert "service_status" in DIAG_TOOLS
    assert diag_command("process_list", conn_type="ssh")
    assert diag_command("log_search", conn_type="winrm", pattern="error")


def test_refresh_usage_metrics(tmp_path: Path, monkeypatch):
    audit = tmp_path / "platform_audit.jsonl"
    metrics = tmp_path / "usage" / "metrics.json"
    hosts = tmp_path / "hosts.json"
    hosts.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")
    reset_platform_audit_for_tests(audit)
    append_platform_audit(
        "fleet_execute",
        trace_id="t1",
        outcome="success",
        path=audit,
    )
    append_platform_audit(
        "ai_diagnosis",
        trace_id="t2",
        outcome="success",
        path=audit,
    )
    monkeypatch.setenv("PLATFORM_AUDIT_FILE", str(audit))
    monkeypatch.setenv("USAGE_METRICS_FILE", str(metrics))
    # 指向临时 hosts：通过改 repo_root 太重；直接测 refresh 写入 + count 事件
    reset_platform_audit_for_tests(audit)
    snap = refresh_usage_metrics(path=metrics)
    assert metrics.is_file()
    assert snap["fleet_executions_last_30d"] >= 1
    assert snap["diagnosis_last_30d"] >= 1
    again = collect_anonymous_metrics()
    assert "total_hosts" in again
    reset_platform_audit_for_tests(None)
