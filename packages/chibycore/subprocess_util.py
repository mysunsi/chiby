"""POSIX 子进程超时清理：setsid / start_new_session + killpg，降低僵尸与孤儿进程。"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Windows 无 SIGKILL；POSIX 路径仍用真实 SIGKILL；此处仅为默认参数与注解求值安全。
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def posix_start_new_session_kwargs() -> dict[str, Any]:
    """非 Windows 下为 shell 子进程创建独立会话，便于按进程组回收。"""
    if sys.platform == "win32":
        return {}
    return {"start_new_session": True}


def kill_process_group_posix(pid: Optional[int], *, sig: int = _SIGKILL) -> None:
    if pid is None or pid <= 0 or sys.platform == "win32":
        return
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass
    except OSError as e:
        if getattr(e, "errno", None) != 3:  # ESRCH
            logger.debug("killpg pid=%s: %s", pid, e)


def terminate_process_tree_sync(proc: Optional[subprocess.Popen], *, grace_sec: float = 0.0) -> None:
    """终止同步 ``subprocess.Popen``：POSIX 优先 ``SIGKILL`` 整个进程组。"""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return
    kill_process_group_posix(pid, sig=_SIGKILL)
    try:
        proc.wait(timeout=max(grace_sec, 2.0))
    except Exception:
        pass


async def terminate_asyncio_process_tree(proc: Optional[asyncio.subprocess.Process]) -> None:
    """终止 ``asyncio.create_subprocess_*`` 进程：POSIX 使用 killpg。"""
    if proc is None:
        return
    if proc.returncode is not None:
        return
    pid = proc.pid
    if sys.platform == "win32":
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        return
    kill_process_group_posix(pid, sig=_SIGKILL)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        pass
