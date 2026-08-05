# -*- coding: utf-8 -*-
"""命令风险分级：变更至少 MEDIUM；高危 HIGH；只读 LOW。"""
from __future__ import annotations

from terminal.llm_shell import (
    apply_prompt_result_risk,
    classify_command_risk,
    looks_like_mutating_command,
)
from terminal.models import PromptResult


def test_rm_file_is_medium():
    level, _ = classify_command_risk("rm a.dat")
    assert level == "MEDIUM"
    assert looks_like_mutating_command("rm a.dat")


def test_rm_rf_is_high():
    level, warn = classify_command_risk("rm -rf /tmp/x")
    assert level == "HIGH"
    assert "危险" in warn or "危险" in (warn or "") or warn


def test_readonly_is_low():
    for cmd in ("free -h", "df -h", "ps aux", "Get-Process", "uptime"):
        level, _ = classify_command_risk(cmd)
        assert level == "LOW", cmd


def test_redirect_write_is_medium():
    level, _ = classify_command_risk("echo hi > /tmp/x.txt")
    assert level == "MEDIUM"


def test_apply_prompt_result_sets_confirm_for_medium():
    r = PromptResult(command="rm a.dat", should_execute=True)
    out = apply_prompt_result_risk(r)
    assert out.confirm_required is True
    assert out.is_dangerous is False


def test_apply_prompt_result_high():
    r = PromptResult(command="rm -rf /var/tmp/x", should_execute=True)
    out = apply_prompt_result_risk(r)
    assert out.confirm_required is True
    assert out.is_dangerous is True


def test_apply_prompt_result_low_no_confirm():
    r = PromptResult(command="free -h", should_execute=True)
    out = apply_prompt_result_risk(r)
    assert out.confirm_required is False
    assert out.is_dangerous is False
