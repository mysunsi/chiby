"""WinRM Shell：无 WebSocket 时延迟释放，避免远端 wsmprovhost 堆积。"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from terminal.models import ConnType, SessionStatus
from terminal.session_manager import SessionManager


@pytest.mark.asyncio
async def test_winrm_shell_released_after_ws_disconnect(monkeypatch):
    monkeypatch.setenv("OPS_WINRM_SHELL_RELEASE_SEC", "0.05")
    mgr = SessionManager()
    sess = mgr.create_session(
        title="winrm-test",
        conn_type=ConnType.WINRM,
        host="127.0.0.1",
        username="u",
        password="p",
    )
    sid = sess.id
    shell = MagicMock()
    shell.close = AsyncMock()
    mgr._shells[sid] = shell
    mgr._sessions[sid].status = SessionStatus.CONNECTED

    ws = object()
    mgr.register_ws(sid, ws)
    mgr.unregister_ws(sid, ws)
    assert not mgr.has_ws(sid)
    mgr.schedule_winrm_shell_release(sid)

    await asyncio.sleep(0.2)
    assert sid not in mgr._shells
    shell.close.assert_awaited()
    assert mgr._sessions[sid].status == SessionStatus.DISCONNECTED


@pytest.mark.asyncio
async def test_winrm_shell_release_cancelled_on_reconnect(monkeypatch):
    monkeypatch.setenv("OPS_WINRM_SHELL_RELEASE_SEC", "0.3")
    mgr = SessionManager()
    sess = mgr.create_session(
        title="winrm-test2",
        conn_type=ConnType.WINRM,
        host="127.0.0.1",
        username="u",
        password="p",
    )
    sid = sess.id
    shell = MagicMock()
    shell.close = AsyncMock()
    mgr._shells[sid] = shell
    mgr._sessions[sid].status = SessionStatus.CONNECTED

    ws1 = object()
    mgr.register_ws(sid, ws1)
    mgr.unregister_ws(sid, ws1)
    mgr.schedule_winrm_shell_release(sid)

    await asyncio.sleep(0.05)
    ws2 = object()
    mgr.register_ws(sid, ws2)  # 取消待释放

    await asyncio.sleep(0.4)
    assert sid in mgr._shells
    shell.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_ssh_not_auto_released_on_ws_disconnect(monkeypatch):
    monkeypatch.setenv("OPS_WINRM_SHELL_RELEASE_SEC", "0.05")
    mgr = SessionManager()
    sess = mgr.create_session(
        title="ssh-test",
        conn_type=ConnType.SSH,
        host="127.0.0.1",
        username="u",
        password="p",
    )
    sid = sess.id
    shell = MagicMock()
    shell.close = AsyncMock()
    mgr._shells[sid] = shell
    mgr._sessions[sid].status = SessionStatus.CONNECTED

    ws = object()
    mgr.register_ws(sid, ws)
    mgr.unregister_ws(sid, ws)
    mgr.schedule_winrm_shell_release(sid)
    await asyncio.sleep(0.15)
    assert sid in mgr._shells
    shell.close.assert_not_awaited()
