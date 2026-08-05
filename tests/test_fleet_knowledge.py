"""Fleet 报告与知识库联动。"""
from __future__ import annotations

from chibyterm.fleet_knowledge import (
    build_fleet_search_query,
    detect_repeat_failure_pattern,
    prefill_fleet_kb_template,
)
from chibycore.knowledge_hub.models import IngestSource


def test_ingest_source_has_fleet_report():
    assert IngestSource.FLEET_REPORT.value == "fleet_report"


def test_build_fleet_search_query():
    q = build_fleet_search_query(
        nl_intent="检查内存",
        command="free -h",
        report_md="## 摘要\n内存使用异常偏高\n正常项忽略",
    )
    assert "内存" in q or "free" in q


def test_prefill_fleet_kb_template():
    pref = prefill_fleet_kb_template(
        nl_intent="巡检 CPU",
        command="uptime",
        report_md="## 根因建议\n负载偏高，建议扩容\n",
        host_scope="生产-Web（3台）",
        stats={"ok": 2, "fail": 1, "total": 3},
        job_id="job_abc",
        report_tone="ops",
    )
    assert pref["source"] == "fleet_report"
    assert pref["trace_id"] == "job_abc"
    assert "fleet_report" in pref["tags"]
    assert "生产-Web" in pref["host_scope"]
    assert "扩容" in pref["root_cause"] or "负载" in pref["root_cause"]


def test_detect_repeat_failure_consecutive():
    events = [
        {
            "event_type": "scheduled_task_run",
            "outcome": "failure",
            "command": "df -h",
            "result_summary": "fail 1",
            "trace_id": "t3",
            "metadata": {"name": "disk", "schedule_id": "s1"},
            "host_ids": ["h1"],
        },
        {
            "event_type": "scheduled_task_run",
            "outcome": "failure",
            "command": "df -h",
            "result_summary": "fail 2",
            "trace_id": "t2",
            "metadata": {"name": "disk", "schedule_id": "s1"},
            "host_ids": ["h1"],
        },
        {
            "event_type": "scheduled_task_run",
            "outcome": "failure",
            "command": "df -h",
            "result_summary": "fail 3",
            "trace_id": "t1",
            "metadata": {"name": "disk", "schedule_id": "s1"},
            "host_ids": ["h1"],
        },
    ]
    hint = detect_repeat_failure_pattern(events, min_repeats=3)
    assert hint is not None
    assert hint["count"] == 3
    assert "连续 3 次" in hint["hint"]

    # 中间插入成功则打断
    broken = [
        events[0],
        {"event_type": "scheduled_task_run", "outcome": "success", "metadata": {}},
        events[1],
        events[2],
    ]
    assert detect_repeat_failure_pattern(broken, min_repeats=3) is None
