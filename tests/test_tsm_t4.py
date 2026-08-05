"""TSM-A T4：记住此类 · 取证半自动重放 · 审计冷归档。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from terminal.mobile.audit_archive import archive_mobile_audit, doctor_audit_archive
from terminal.mobile.confirm_pref import (
    doctor_remember,
    match_remember_pref,
    remember_enabled,
    risk_allowed_for_remember,
    save_remember_pref,
)
from terminal.mobile.tsm import (
    build_forensic_replay_plan,
    extract_forensic_commands,
)


def test_risk_allowed_for_remember():
    assert risk_allowed_for_remember("low")
    assert risk_allowed_for_remember("medium")
    assert not risk_allowed_for_remember("high")


def test_remember_pref_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_TSM_REMEMBER_CONFIRM", "1")
    monkeypatch.setenv("OPS_TSM_REMEMBER_TTL_HOURS", "1")
    path = tmp_path / "prefs.json"
    assert remember_enabled()
    pref = save_remember_pref(
        user_id="u1",
        operation_type="shell_mutate",
        host_id="h1",
        risk_level="medium",
        path=path,
    )
    assert pref is not None
    assert match_remember_pref(
        user_id="u1",
        operation_type="shell_mutate",
        host_id="h1",
        risk_level="medium",
        path=path,
    )
    # 高危不可匹配
    assert not match_remember_pref(
        user_id="u1",
        operation_type="shell_mutate",
        host_id="h1",
        risk_level="high",
        path=path,
    )
    # 高危不可写入
    assert (
        save_remember_pref(
            user_id="u1",
            operation_type="rm",
            host_id="h1",
            risk_level="high",
            path=path,
        )
        is None
    )


def test_remember_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_TSM_REMEMBER_CONFIRM", "0")
    path = tmp_path / "prefs.json"
    assert not remember_enabled()
    assert (
        save_remember_pref(
            user_id="u1",
            operation_type="shell",
            host_id="",
            risk_level="low",
            path=path,
        )
        is None
    )
    d = doctor_remember()
    assert d["enabled"] is False
    assert d["ok"] is True


def test_extract_and_plan_forensic_replay():
    bundle = {
        "conversation_id": "c1",
        "turn_id": "t1",
        "trace_id": "t1",
        "steps": [
            {"command": "uptime", "host": "h1", "event": "exec_done"},
            {"command": "systemctl restart nginx", "host": "h1", "event": "permission_allow_exec"},
            {"command": "uptime", "host": "h1", "event": "exec_done"},
        ],
    }
    cmds = extract_forensic_commands(bundle)
    assert len(cmds) == 2
    assert cmds[0]["command"] == "uptime"
    plan = build_forensic_replay_plan(
        bundle,
        readonly_cmds=["uptime"],
        mutate_cmds=["systemctl restart nginx"],
        dry_run=True,
    )
    assert plan["ok"]
    assert plan["readonly_count"] == 1
    assert plan["mutate_skipped_count"] == 1
    assert plan["mutate_skipped"][0]["action"] == "display_only"


def test_audit_archive_moves_old_rows(tmp_path):
    audit = tmp_path / "mobile_audit.jsonl"
    arch = tmp_path / "audit_archive"
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    hot = (now - timedelta(days=1)).isoformat()
    audit.write_text(
        "\n".join(
            [
                '{"ts":"%s","event":"old","payload":{}}' % old,
                '{"ts":"%s","event":"new","payload":{}}' % hot,
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = archive_mobile_audit(
        audit_path=audit,
        archive_dir=arch,
        hot_days=14,
        now=now,
    )
    assert result["ok"]
    assert result["moved"] == 1
    assert result["kept"] == 1
    kept = audit.read_text(encoding="utf-8")
    assert "new" in kept
    assert "old" not in kept
    month = (now - timedelta(days=30)).strftime("%Y%m")
    cold = arch / f"mobile_audit_{month}.jsonl"
    assert cold.is_file()
    assert "old" in cold.read_text(encoding="utf-8")
    d = doctor_audit_archive()
    assert d["ok"] and d["hot_days"] >= 1
