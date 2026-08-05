"""闭环超时中断提示含「继续」指引。"""

from __future__ import annotations

from terminal.mobile.orchestrator import _closure_interrupt_user_note


def test_timeout_note_mentions_continue():
    note = _closure_interrupt_user_note("掌上AI大脑 回合超时（>180s）")
    assert "闭环中断" in note
    assert "以上远端结果仍有效" in note
    assert "继续" in note
    assert "断点" in note or "续跑" in note


def test_generic_interrupt_still_hints_continue():
    note = _closure_interrupt_user_note("规划引擎暂不可用")
    assert "继续" in note
