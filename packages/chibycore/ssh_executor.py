"""SSH 执行器：通过 sshpass + subprocess 执行远程命令。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass

from .config import CMD_TIMEOUT
from .subprocess_util import posix_start_new_session_kwargs, terminate_process_tree_sync


@dataclass
class CmdResult:
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    success: bool

    @property
    def output(self) -> str:
        return self.stdout + ("\n" + self.stderr if self.stderr else "")


def exec_ssh(
    host: str,
    command: str,
    user: str,
    password: str,
    timeout: int = CMD_TIMEOUT,
) -> CmdResult:
    """通过 sshpass 执行远程 SSH 命令。"""
    t0 = time.time()

    cmd = [
        "sshpass",
        "-p", password,
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", f"ConnectTimeout={timeout}",
        "-o", "BatchMode=yes",
        f"{user}@{host}",
        command,
    ]

    run_kw = dict(
        capture_output=True,
        text=True,
        timeout=timeout + 5,
    )
    run_kw.update(posix_start_new_session_kwargs())

    try:
        proc = subprocess.run(
            cmd,
            **run_kw,
        )
        duration_ms = int((time.time() - t0) * 1000)
        return CmdResult(
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            exit_code=proc.returncode,
            duration_ms=duration_ms,
            success=proc.returncode == 0,
        )
    except subprocess.TimeoutExpired as ex:
        duration_ms = int((time.time() - t0) * 1000)
        proc = getattr(ex, "process", None)
        if proc is not None:
            terminate_process_tree_sync(proc)
        return CmdResult(
            stdout="",
            stderr=f"命令执行超时（{timeout}秒）",
            exit_code=-1,
            duration_ms=duration_ms,
            success=False,
        )
    except Exception as e:
        duration_ms = int((time.time() - t0) * 1000)
        return CmdResult(
            stdout="",
            stderr=str(e),
            exit_code=-1,
            duration_ms=duration_ms,
            success=False,
        )


def exec_local(command: str, timeout: int = CMD_TIMEOUT) -> CmdResult:
    """本地执行命令（不经过 SSH）。POSIX 使用独立会话 + 超时 killpg。"""
    t0 = time.time()
    argv = ["bash", "-c", command]

    if sys.platform == "win32":
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.time() - t0) * 1000)
            return CmdResult(
                stdout=proc.stdout.strip(),
                stderr=proc.stderr.strip(),
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                success=proc.returncode == 0,
            )
        except subprocess.TimeoutExpired:
            return CmdResult(
                stdout="",
                stderr=f"超时（{timeout}秒）",
                exit_code=-1,
                duration_ms=int((time.time() - t0) * 1000),
                success=False,
            )
        except Exception as e:
            return CmdResult(
                stdout="",
                stderr=str(e),
                exit_code=-1,
                duration_ms=int((time.time() - t0) * 1000),
                success=False,
            )

    kw = posix_start_new_session_kwargs()
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **kw,
        )
        try:
            so, se = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_tree_sync(proc)
            return CmdResult(
                stdout="",
                stderr=f"超时（{timeout}秒）",
                exit_code=-1,
                duration_ms=int((time.time() - t0) * 1000),
                success=False,
            )
        duration_ms = int((time.time() - t0) * 1000)
        rc = proc.returncode if proc.returncode is not None else -1
        return CmdResult(
            stdout=(so or "").strip(),
            stderr=(se or "").strip(),
            exit_code=rc,
            duration_ms=duration_ms,
            success=rc == 0,
        )
    except Exception as e:
        return CmdResult(
            stdout="",
            stderr=str(e),
            exit_code=-1,
            duration_ms=int((time.time() - t0) * 1000),
            success=False,
        )
