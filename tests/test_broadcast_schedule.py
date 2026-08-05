"""Fleet schedule: next_run 与 CRUD。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chibyterm.broadcast_schedule import (
    compute_next_run_at,
    create_schedule,
    delete_schedule,
    get_schedule,
    list_due_schedules,
    normalize_schedule,
)


def test_compute_next_run_daily_after_now():
    base = datetime(2026, 8, 5, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    nxt = compute_next_run_at(freq="daily", time_hhmm="08:00", after=base)
    ts = datetime.fromisoformat(nxt)
    assert ts.day == 6
    assert ts.hour == 8


def test_compute_next_run_weekly():
    # 2026-08-05 是周三；要下周一
    base = datetime(2026, 8, 5, 10, 0, tzinfo=timezone(timedelta(hours=8)))
    nxt = compute_next_run_at(freq="weekly", time_hhmm="09:00", weekday=0, after=base)
    ts = datetime.fromisoformat(nxt)
    assert ts.weekday() == 0
    assert ts.hour == 9


def test_schedule_crud_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chibyterm.broadcast_schedule._schedules_path",
        lambda: tmp_path / "broadcast_schedules.json",
    )
    item = create_schedule(
        {
            "name": "晨检",
            "freq": "daily",
            "time": "08:30",
            "host_ids": ["h1", "h2"],
            "nl_intent": "查看内存",
            "report_tone": "capacity",
        }
    )
    assert item["id"]
    assert item["next_run_at"]
    got = get_schedule(item["id"])
    assert got and got["name"] == "晨检"
    assert delete_schedule(item["id"]) is True
    assert get_schedule(item["id"]) is None


def test_list_due_includes_past(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "chibyterm.broadcast_schedule._schedules_path",
        lambda: tmp_path / "broadcast_schedules.json",
    )
    past = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat()
    create_schedule(
        {
            "name": "due",
            "freq": "daily",
            "time": "00:00",
            "host_ids": ["h1"],
            "nl_intent": "x",
            "next_run_at": past,
            "enabled": True,
        }
    )
    # normalize may overwrite next_run_at when creating — force past via file
    from chibyterm.broadcast_schedule import load_schedules, _save_all

    items = load_schedules()
    items[0]["next_run_at"] = past
    _save_all(items)
    due = list_due_schedules()
    assert any(x.get("name") == "due" for x in due)


def test_normalize_fail_policy():
    s = normalize_schedule({"fail_policy": "all_ok_only", "host_ids": ["a"]})
    assert s["fail_policy"] == "all_ok_only"
