"""第一期 P0：熔断 + 变更白名单 + 写文件预览。"""

from __future__ import annotations

import pytest

from terminal.mobile.exec_guardrails import (
    ExecBreakers,
    format_write_file_diff_preview,
    is_auto_mutate_allowed,
    reset_auto_mutate_cache,
)
from terminal.mobile.remote_tools import RemoteToolCall, call_needs_confirmation


@pytest.fixture(autouse=True)
def _reset_patterns(monkeypatch):
    reset_auto_mutate_cache()
    monkeypatch.delenv("OPS_MOBILE_AUTO_MUTATE_ALLOW", raising=False)
    yield
    reset_auto_mutate_cache()


def test_auto_mutate_whitelist_defaults():
    assert is_auto_mutate_allowed("systemctl restart nginx") is True
    assert is_auto_mutate_allowed("sudo systemctl reload nginx") is True
    assert is_auto_mutate_allowed("nginx -s reload") is True
    assert is_auto_mutate_allowed("chmod 644 /tmp/a") is True
    # stop 受控但不在默认白名单
    assert is_auto_mutate_allowed("systemctl stop nginx") is False
    assert is_auto_mutate_allowed("rm -rf /tmp/x") is False


def test_auto_mutate_allow_off(monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_AUTO_MUTATE_ALLOW", "0")
    reset_auto_mutate_cache()
    assert is_auto_mutate_allowed("systemctl restart nginx") is False


def test_call_needs_confirmation_whitelist_gap():
    # 全能型：白名单内 restart 自动；stop 仍确认
    assert (
        call_needs_confirmation(
            RemoteToolCall(tool="ssh_execute", command="systemctl restart nginx"),
            confirm_changes=False,
        )
        is False
    )
    assert (
        call_needs_confirmation(
            RemoteToolCall(tool="ssh_execute", command="systemctl stop nginx"),
            confirm_changes=False,
        )
        is True
    )


def test_write_file_diff_preview():
    preview = format_write_file_diff_preview("/tmp/a.txt", "line1\nline2\n")
    assert "remote_write_file → /tmp/a.txt" in preview
    assert "line1" in preview
    assert "拟写入内容" in preview
    assert "2 行" in preview


def test_host_breaker_trips_and_blocks():
    b = ExecBreakers(host_fail_threshold=2, host_cool_sec=60.0)
    assert b.host_block_reason("h1") is None
    assert b.note_host("h1", ok=False) is None
    trip = b.note_host("h1", ok=False)
    assert trip is not None
    assert "熔断" in trip
    assert b.host_block_reason("h1") is not None


def test_host_breaker_half_open_clears_cool():
    b = ExecBreakers(host_fail_threshold=2, host_cool_sec=60.0)
    b.note_host("h1", ok=False)
    b.note_host("h1", ok=False)
    assert b.host_block_reason("h1") is not None
    b.half_open_host("h1")
    assert b.host_block_reason("h1") is None
    # 半开后需重新累计失败才会再熔断
    assert b.note_host("h1", ok=False) is None
    assert b.host_block_reason("h1") is None


def test_hermes_breaker_trips():
    b = ExecBreakers(hermes_fail_threshold=2, hermes_cool_sec=30.0)
    assert b.note_hermes(ok=False) is None
    trip = b.note_hermes(ok=False)
    assert trip is not None
    assert b.hermes_block_reason() is not None
    b.note_hermes(ok=True)
    assert b.hermes_block_reason() is None


def test_hermes_breaker_half_open():
    b = ExecBreakers(hermes_fail_threshold=2, hermes_cool_sec=30.0)
    b.note_hermes(ok=False)
    b.note_hermes(ok=False)
    assert b.hermes_block_reason() is not None
    b.half_open_hermes()
    assert b.hermes_block_reason() is None