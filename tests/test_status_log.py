# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path


def test_append_status_log(tmp_path, monkeypatch):
    from terminal.mobile import status_log as sl

    path = tmp_path / "status.log"
    monkeypatch.setenv("OPS_MOBILE_STATUS_LOG", str(path))
    monkeypatch.setenv("OPS_MOBILE_STATUS_LOG_ENABLE", "1")

    assert sl.append_status_log(
        "全能型: Chiby 原样流式 (winrm 无头) ...可点停止",
        conversation_id="c1",
        agent_mode="omnipotent",
        source="client",
    )
    assert sl.append_status_log(
        "智能型：交由 Chiby（执行将走 ssh 无头平面）...可点停止",
        conversation_id="c1",
        agent_mode="intelligent",
    )
    text = path.read_text(encoding="utf-8")
    assert "原样流式" in text
    assert "交由 Chiby" in text
    assert "omnipotent" in text
    assert text.count("\n") >= 2


def test_status_log_disabled(tmp_path, monkeypatch):
    from terminal.mobile import status_log as sl

    path = tmp_path / "status.log"
    monkeypatch.setenv("OPS_MOBILE_STATUS_LOG", str(path))
    monkeypatch.setenv("OPS_MOBILE_STATUS_LOG_ENABLE", "0")
    assert sl.append_status_log("x") is False
    assert not path.exists()
