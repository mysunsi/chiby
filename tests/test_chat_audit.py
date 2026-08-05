# -*- coding: utf-8 -*-
"""完整聊天审计 + 时间显示规则相关测试。"""

from __future__ import annotations

from pathlib import Path

from terminal.mobile.chat_audit import (
    append_chat_audit,
    chat_audit_enabled,
    doctor_chat_audit,
    read_chat_audit,
)


def test_append_and_read_chat_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_CHAT_AUDIT", "1")
    monkeypatch.setenv("OPS_MOBILE_CHAT_AUDIT_DIR", str(tmp_path))
    assert chat_audit_enabled()
    ok = append_chat_audit(
        "demo-conv-audit1",
        "user",
        "看看桌面有什么",
        channel="demo",
        source="server",
    )
    assert ok
    ok2 = append_chat_audit(
        "demo-conv-audit1",
        "bot",
        "`$ ls` · 已成功执行\n<<<EXEC_BODY>>>\nfile1\n<<<END_EXEC_BODY>>>",
        kind="stream",
        source="client",
    )
    assert ok2
    rows = read_chat_audit("demo-conv-audit1", limit=20)
    assert len(rows) == 2
    assert rows[0]["role"] == "user"
    assert "桌面" in rows[0]["text"]
    assert rows[1]["role"] == "bot"
    assert "<<<EXEC_BODY>>>" in rows[1]["text"]
    doc = doctor_chat_audit()
    assert doc["enabled"] is True
    assert doc["files"] >= 1
    assert (tmp_path / "demo-conv-audit1.jsonl").is_file()


def test_chat_audit_truncates_huge(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_CHAT_AUDIT", "1")
    monkeypatch.setenv("OPS_MOBILE_CHAT_AUDIT_DIR", str(tmp_path))
    monkeypatch.setenv("OPS_MOBILE_CHAT_AUDIT_MAX_CHARS", "10000")
    huge = "x" * 50_000
    append_chat_audit("demo-conv-huge", "bot", huge, source="server")
    rows = read_chat_audit("demo-conv-huge")
    assert len(rows) == 1
    assert rows[0]["truncated"] is True
    assert "审计落盘截断" in rows[0]["text"]
    assert len(rows[0]["text"]) < 20_000
