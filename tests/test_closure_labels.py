# -*- coding: utf-8 -*-
"""闭环用户可读文案。"""
from __future__ import annotations

from chibycore.closure_labels import (
    format_both_mode_detail,
    format_verify_message,
    humanize_judge_reason,
    humanize_outcome_detail,
)


def test_humanize_parse_fail_code():
    assert "退出码" in humanize_judge_reason("llm_parse_fail_exit_code_fallback")
    assert "llm_parse_fail" not in humanize_judge_reason("llm_parse_fail_exit_code_fallback")


def test_format_both_mode_detail():
    s = format_both_mode_detail(
        exit_ok=False,
        llm_ok=False,
        reason="llm_parse_fail_exit_code_fallback",
    )
    assert "退出码未通过" in s
    assert "智能判定未通过" in s
    assert "exit=" not in s
    assert "llm_parse_fail" not in s


def test_humanize_legacy_outcome_detail():
    legacy = "exit=False llm=False (llm_parse_fail_exit_code_fallback)"
    out = humanize_outcome_detail(legacy)
    assert "退出码未通过" in out
    assert "exit=" not in out


def test_format_verify_includes_stderr():
    msg = format_verify_message(
        passed=False,
        exit_ok=False,
        llm_ok=False,
        success_mode="both",
        judge_reason="llm_parse_fail_exit_code_fallback",
        stderr_tail="rm: cannot remove 'a.dat': No such file or directory",
    )
    assert msg.startswith("验证未通过")
    assert "a.dat" in msg
    assert "llm_parse_fail" not in msg


def test_format_verify_pass_with_stdout():
    msg = format_verify_message(
        passed=True,
        exit_ok=True,
        llm_ok=True,
        success_mode="both",
        judge_reason="智能判定结果未能解析，已改按退出码判断",
        stdout_tail="already_absent",
    )
    assert msg.startswith("验证通过")
    assert "already_absent" in msg
