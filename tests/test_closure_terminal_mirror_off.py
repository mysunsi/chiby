# -*- coding: utf-8 -*-
"""闭环默认不写入左侧终端。"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from chibycore.closure_capture_mirror import (
    closure_terminal_mirror_enabled,
    format_mirror_step_footer_streaming,
    mirror_closure_io_to_terminal,
    mirror_closure_step_after_streaming,
    mirror_closure_step_to_session,
)
from chibycore.executor_contract import ClosurePayload, RiskLevel


def test_terminal_mirror_default_off(monkeypatch):
    monkeypatch.delenv("OPS_CLOSURE_MIRROR_TERMINAL", raising=False)
    assert closure_terminal_mirror_enabled() is False


def test_terminal_mirror_opt_in(monkeypatch):
    monkeypatch.setenv("OPS_CLOSURE_MIRROR_TERMINAL", "1")
    assert closure_terminal_mirror_enabled() is True


def test_mirror_helpers_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OPS_CLOSURE_MIRROR_TERMINAL", raising=False)
    mgr = MagicMock()
    loop = MagicMock()
    mirror_closure_io_to_terminal(mgr, loop, "sid", "stdout", "hello")
    cp = ClosurePayload(
        trace_id="t",
        raw_command="x",
        effective_command="x",
        transport="ssh",
        risk_level=RiskLevel.LOW,
        exit_code=0,
        stdout="ok",
        stderr="",
    )
    step = SimpleNamespace(
        phase="initial",
        fix_round=0,
        gateway_allowed=True,
        command="echo ok",
        payload=cp,
        gateway_reason="",
        gateway_detail=None,
    )
    mirror_closure_step_after_streaming(mgr, loop, "sid", step)
    mirror_closure_step_to_session(mgr, "sid", step)
    mgr.schedule_terminal_output.assert_not_called()
    mgr.append_output_capture.assert_not_called()


def test_footer_formatter_still_available():
    cp = ClosurePayload(
        trace_id="t",
        raw_command="x",
        effective_command="x",
        transport="ssh",
        risk_level=RiskLevel.LOW,
        exit_code=1,
        stdout="",
        stderr="err",
    )
    step = SimpleNamespace(
        phase="initial",
        fix_round=0,
        gateway_allowed=True,
        command="rm a.dat",
        payload=cp,
    )
    footer = format_mirror_step_footer_streaming(step)
    assert "闭环" in footer
    assert "rm a.dat" in footer
