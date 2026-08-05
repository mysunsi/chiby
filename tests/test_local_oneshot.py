"""本地单次 subprocess 执行器。"""
from __future__ import annotations

import sys

import pytest

from chibycore.local_oneshot import LocalSubprocessOneShotExecutor


def test_local_oneshot_unix_echo():
    ex = LocalSubprocessOneShotExecutor("unix")
    ex.connect()
    try:
        r = ex.run_command("echo OK_TEST")
    finally:
        ex.close()
    assert r.exit_code == 0
    assert "OK_TEST" in (r.stdout + r.stderr)


def test_local_oneshot_powershell_echo():
    if sys.platform != "win32":
        pytest.skip("PowerShell path主要针对 Windows")
    ex = LocalSubprocessOneShotExecutor("powershell")
    ex.connect()
    try:
        r = ex.run_command("Write-Output PS_OK")
    finally:
        ex.close()
    assert r.exit_code == 0
    assert "PS_OK" in (r.stdout + r.stderr)
