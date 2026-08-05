"""群发 job：exec_done 后无报告，按需再生成（规则降级）。"""
from __future__ import annotations

from chibyterm.broadcast_report import (
    BroadcastHostResult,
    BroadcastJob,
    comparative_report_md,
    get_broadcast_job,
    job_to_api_dict,
    store_broadcast_job,
)


def test_job_exec_done_phase_has_no_report_until_set():
    job = BroadcastJob(
        job_id="j1",
        command="free -h",
        phase="exec_done",
        results=[
            BroadcastHostResult(
                session_id="a", host_label="a", status="pass", ok=True, explain_md="ok"
            )
        ],
    )
    store_broadcast_job(job)
    got = get_broadcast_job("j1")
    assert got is not None
    assert got.phase == "exec_done"
    assert not (got.report_md or "").strip()
    d = job_to_api_dict(got)
    assert d["phase"] == "exec_done"
    assert d["report_md"] == ""


def test_comparative_report_on_demand(monkeypatch):
    from chibycore import llm_providers as lp

    class _Empty:
        is_available = False

    monkeypatch.setattr(lp, "get_llm", lambda: _Empty())
    md = comparative_report_md(
        command="free -h",
        results=[
            BroadcastHostResult(
                session_id="a",
                host_label="web",
                status="pass",
                ok=True,
                explain_md="内存正常",
            )
        ],
        report_tone="ops",
    )
    assert "总体结论" in md or "总览" in md or "web" in md
