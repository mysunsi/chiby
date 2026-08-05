"""多主机 HostTabs：扇出事件顺序与 Hermes 回灌包。"""

from __future__ import annotations

import pytest

from terminal.mobile.headless_exec import FakeHeadlessExecutor
from terminal.mobile.job_orchestrator import (
    JobOrchestrator,
    format_job_feedback_for_hermes,
    host_panel_snapshot,
)
from terminal.mobile.models import HostSummary


def _hosts() -> list[HostSummary]:
    return [
        HostSummary(id="host-a", name="A", host="10.0.0.1", conn_type="ssh"),
        HostSummary(id="host-b", name="B", host="10.0.0.2", conn_type="ssh"),
        HostSummary(id="host-c", name="C", host="10.0.0.3", conn_type="ssh"),
    ]


@pytest.mark.asyncio
async def test_host_panel_event_sequence():
    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    orch = JobOrchestrator(FakeHeadlessExecutor(), default_timeout_sec=30.0)
    run = await orch.run(
        name="检测 nginx",
        host_ids=["host-a", "host-b", "host-c"],
        commands=["systemctl is-active nginx"],
        hosts=_hosts(),
        max_parallel=2,
        on_event=on_event,
    )
    assert run.status == "done"
    types = [e.get("type") for e in events]
    assert "host_panel" in types
    assert "host_task" in types
    assert "host_panel_done" in types

    panel = next(e for e in events if e["type"] == "host_panel")
    assert panel["run_id"] == run.run_id
    assert len(panel["hosts"]) == 3
    assert panel["commands_preview"]

    # 每台至少有 pending → 终态
    host_events = [e for e in events if e.get("type") == "host_task"]
    by_host: dict[str, list[str]] = {}
    for e in host_events:
        by_host.setdefault(e["host_id"], []).append(e["status"])
    assert set(by_host) == {"host-a", "host-b", "host-c"}
    for hid, statuses in by_host.items():
        assert "pending" in statuses or statuses[0] == "pending"
        assert statuses[-1] in ("ok", "fail", "cancelled")

    done = next(e for e in events if e["type"] == "host_panel_done")
    assert done["ok_count"] == 3
    assert done["fail_count"] == 0
    assert done["total"] == 3
    assert done.get("barrier") is True
    assert done["snapshot"]["run_id"] == run.run_id

    barrier = next(e for e in events if e["type"] == "host_panel_barrier")
    assert barrier["total"] == 3
    # 屏障必须在任意 host_task 终态之后、host_panel_done 之前
    idx_barrier = types.index("host_panel_barrier")
    idx_done = types.index("host_panel_done")
    assert idx_barrier < idx_done
    last_task_idx = max(i for i, t in enumerate(types) if t == "host_task")
    assert last_task_idx < idx_barrier


@pytest.mark.asyncio
async def test_slow_host_does_not_end_job_early():
    """某台先完成不会提前结束整轮；须等 gather 屏障。"""
    import asyncio

    class StaggerExec(FakeHeadlessExecutor):
        async def run(self, host_id, command, **kwargs):  # type: ignore[no-untyped-def]
            delay = {"host-a": 0.01, "host-b": 0.12, "host-c": 0.05}.get(host_id, 0.02)
            await asyncio.sleep(delay)
            return await super().run(host_id, command, **kwargs)

    events: list[dict] = []
    first_ok_at: list[float] = []
    barrier_at: list[float] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)
        if ev.get("type") == "host_task" and ev.get("status") == "ok" and not first_ok_at:
            first_ok_at.append(asyncio.get_running_loop().time())
        if ev.get("type") == "host_panel_barrier":
            barrier_at.append(asyncio.get_running_loop().time())

    orch = JobOrchestrator(StaggerExec(), default_timeout_sec=30.0)
    run = await orch.run(
        name="stagger",
        host_ids=["host-a", "host-b", "host-c"],
        commands=["uptime"],
        hosts=_hosts(),
        max_parallel=3,
        on_fail="continue",
        on_event=on_event,
    )
    assert run.status == "done"
    assert all(t.status == "ok" for t in run.tasks)
    assert first_ok_at and barrier_at
    assert barrier_at[0] >= first_ok_at[0]
    # 首台 ok 之后仍应有其它 host_task
    types = [e.get("type") for e in events]
    idx_first_ok = next(
        i
        for i, e in enumerate(events)
        if e.get("type") == "host_task" and e.get("status") == "ok"
    )
    assert any(
        e.get("type") == "host_task" and e.get("status") in ("running", "pending", "ok")
        for e in events[idx_first_ok + 1 :]
    )
    assert "host_panel_barrier" in types
    assert types.index("host_panel_barrier") > idx_first_ok


@pytest.mark.asyncio
async def test_fail_fast_cancels_others():
    class FlakyExec(FakeHeadlessExecutor):
        async def run(self, host_id, command, **kwargs):  # type: ignore[no-untyped-def]
            from terminal.mobile.models import ExecResult

            if host_id == "host-a":
                return ExecResult(
                    host_id=host_id,
                    command=command,
                    ok=False,
                    exit_code=1,
                    stdout_tail="inactive",
                    stderr_tail="",
                    error="inactive",
                    duration_ms=1,
                )
            return await super().run(host_id, command, **kwargs)

    events: list[dict] = []

    async def on_event(ev: dict) -> None:
        events.append(ev)

    orch = JobOrchestrator(FlakyExec(), default_timeout_sec=30.0)
    run = await orch.run(
        name="fail-fast",
        host_ids=["host-a", "host-b", "host-c"],
        commands=["systemctl is-active nginx"],
        hosts=_hosts(),
        max_parallel=1,
        on_fail="fail_fast",
        on_event=on_event,
    )
    statuses = {t.host_id: t.status for t in run.tasks}
    assert statuses["host-a"] == "fail"
    # 其余可能 cancelled（fail_fast）或尚未启动被取消
    assert sum(1 for s in statuses.values() if s == "cancelled") >= 1
    # 即使 fail_fast，仍有整体屏障事件
    assert any(e.get("type") == "host_panel_barrier" for e in events)


def test_hermes_feedback_pack_orders_failures_first():
    from terminal.mobile.job_orchestrator import JobRun, TaskRun

    run = JobRun(
        run_id="run_x",
        name="巡检",
        host_ids=["a", "b"],
        commands=["uptime"],
        tasks=[
            TaskRun(
                task_id="t1",
                host_id="a",
                host_label="A",
                commands=["uptime"],
                status="ok",
                stdout_tail="up 1 day",
                stdout_detail="up 1 day",
            ),
            TaskRun(
                task_id="t2",
                host_id="b",
                host_label="B",
                commands=["uptime"],
                status="fail",
                error="timeout",
                stdout_tail="timeout",
                stdout_detail="timeout",
            ),
        ],
    )
    pack = format_job_feedback_for_hermes(run)
    assert "run_id=run_x" in pack
    assert pack.index("### B") < pack.index("### A")
    assert "display_name: B" in pack
    snap = host_panel_snapshot(run)
    assert snap["fail_count"] == 1
    assert snap["ok_count"] == 1
