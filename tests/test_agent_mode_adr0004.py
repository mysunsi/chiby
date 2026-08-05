"""ADR-0004 模式分层：归一化与策略。"""

from __future__ import annotations

from terminal.mobile.agent_mode import (
    MODE_EFFICIENT,
    MODE_INTELLIGENT,
    MODE_OMNIPOTENT,
    allow_narrow_fast_path,
    effective_remote_tools_enabled,
    normalize_mode,
    parse_mode_switch_phrase,
    policy_for,
    surface_mode,
    uses_hermes,
)


def test_normalize_aliases():
    assert normalize_mode("ops") == MODE_EFFICIENT
    assert normalize_mode("运维模式") == MODE_EFFICIENT
    assert normalize_mode("advanced") == MODE_INTELLIGENT
    assert normalize_mode("高级模式") == MODE_INTELLIGENT
    assert normalize_mode("intelligent") == MODE_INTELLIGENT
    assert normalize_mode("omnipotent") == MODE_OMNIPOTENT
    assert normalize_mode("全能型") == MODE_OMNIPOTENT
    assert normalize_mode("code") == MODE_INTELLIGENT


def test_policy_forces_remote_tools():
    assert effective_remote_tools_enabled("efficient") is False
    assert effective_remote_tools_enabled("ops") is False
    # 智能型 = 旧高级：OPS 闭环，强制关 A2
    assert effective_remote_tools_enabled("intelligent") is False
    assert effective_remote_tools_enabled("advanced") is False
    assert effective_remote_tools_enabled("omnipotent") is True
    assert allow_narrow_fast_path("efficient") is True
    assert allow_narrow_fast_path("intelligent") is False
    assert allow_narrow_fast_path("omnipotent") is False


def test_omnipotent_closed_loop_flag():
    assert policy_for("omnipotent").closed_loop is True
    assert policy_for("intelligent").closed_loop is False
    assert policy_for("omnipotent").confirm_changes is False
    assert policy_for("intelligent").confirm_changes is True
    assert policy_for("intelligent").remote_tools is False
    assert policy_for("omnipotent").remote_tools is True


def test_surface_and_hermes():
    assert uses_hermes("intelligent") and uses_hermes("omnipotent")
    assert not uses_hermes("efficient")
    assert surface_mode("intelligent") == "advanced"
    assert surface_mode("code", coding=True) == "code"
    assert surface_mode("efficient") == "ops"


def test_parse_switch_phrases():
    assert parse_mode_switch_phrase("高效型") == MODE_EFFICIENT
    assert parse_mode_switch_phrase("智能型") == MODE_INTELLIGENT
    assert parse_mode_switch_phrase("全能型") == MODE_OMNIPOTENT
    assert parse_mode_switch_phrase("运维模式") == MODE_EFFICIENT
    assert parse_mode_switch_phrase("高级模式") == MODE_INTELLIGENT
    assert parse_mode_switch_phrase("磁盘还剩多少") == ""
