"""平台统一审计 + 相似案例检索。"""
from __future__ import annotations

from pathlib import Path

from chibycore.platform_audit import (
    append_platform_audit,
    event_type_counts,
    query_platform_audit,
    query_trace,
    reset_platform_audit_for_tests,
)
from chibycore.knowledge_hub.similar_cases import (
    extract_keywords,
    format_similar_cases_prompt,
    format_similar_cases_ui,
)


def test_append_and_query_platform_audit(tmp_path: Path):
    path = tmp_path / "platform_audit.jsonl"
    reset_platform_audit_for_tests(path)
    a = append_platform_audit(
        "fleet_execute",
        trace_id="tr-1",
        user_id="u1",
        host_ids=["h1", "h2"],
        command="uptime",
        result_summary="2/2 ok",
        outcome="success",
        path=path,
    )
    assert a["event_type"] == "fleet_execute"
    assert a["trace_id"] == "tr-1"
    append_platform_audit(
        "ai_diagnosis",
        trace_id="tr-1",
        user_id="u1",
        host_ids=["h1"],
        result_summary="CPU 偏高",
        outcome="partial",
        path=path,
    )
    rows = query_platform_audit(limit=20, path=path)
    assert len(rows) >= 2
    by_type = query_platform_audit(limit=20, event_type="fleet_execute", path=path)
    assert all(r["event_type"] == "fleet_execute" for r in by_type)
    by_host = query_platform_audit(limit=20, host_id="h2", path=path)
    assert any("h2" in (r.get("host_ids") or []) for r in by_host)
    timeline = query_trace("tr-1", path=path)
    assert len(timeline) == 2
    assert event_type_counts(path=path)["fleet_execute"] >= 1
    reset_platform_audit_for_tests(None)


def test_extract_keywords_and_format():
    kws = extract_keywords("帮我看看 Nginx 502 超时是怎么回事")
    assert "nginx" in kws or "502" in kws
    cases = [
        {
            "title": "Nginx 502",
            "root_cause": "php-fpm 内存溢出",
            "solution": "重启 php-fpm",
        }
    ]
    assert "历史相似案例" in format_similar_cases_prompt(cases)
    assert "找到 1 个" in format_similar_cases_ui(cases)
