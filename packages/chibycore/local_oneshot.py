"""本机非交互单次执行：subprocess，与 PTY 会话并行，供本地闭环使用。"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from typing import Any, List, Optional

from chibycore.executor_contract import ExecResult, RunOptions
from chibycore.output_budget import LOCAL_ONESHOT_MAX_COMBINED_OUTPUT_CHARS, truncate_text
from chibycore.subprocess_util import (
    posix_start_new_session_kwargs,
    terminate_process_tree_sync,
)


class LocalSubprocessOneShotExecutor:
    """
    在本机进程内执行 shell 命令（独立子进程，不经当前交互 PTY）。
    ``shell_profile``: ``unix`` → ``/bin/sh -c``；``powershell`` → PowerShell -Command。
    """

    def __init__(self, shell_profile: str = "unix"):
        p = (shell_profile or "unix").strip().lower()
        self._profile = p if p in ("unix", "powershell") else "unix"

    def connect(self) -> None:
        return

    def _argv_for(self, cmd: str) -> List[str]:
        if self._profile == "powershell":
            return [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                cmd,
            ]
        if sys.platform == "win32":
            return ["cmd", "/c", cmd]
        return ["/bin/sh", "-c", cmd]

    def _apply_output_budget(self, out: str, err: str) -> tuple[str, str, bool]:
        total = len(out) + len(err)
        cap = LOCAL_ONESHOT_MAX_COMBINED_OUTPUT_CHARS
        if total <= cap:
            return out, err, False
        half = max(1024, cap // 2)
        o2, t1 = truncate_text(out, half)
        e2, t2 = truncate_text(err, half)
        return o2, e2, t1 or t2

    def _run_command_blocking(self, cmd: str, timeout: float, tid: str, t0: float) -> ExecResult:
        argv = self._argv_for(cmd)
        posix_kw = posix_start_new_session_kwargs()
        try:
            if sys.platform == "win32":
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    shell=False,
                )
                rc = proc.returncode
                out = proc.stdout or ""
                err = proc.stderr or ""
            else:
                proc = subprocess.Popen(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    **posix_kw,
                )
                try:
                    out, err = proc.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    terminate_process_tree_sync(proc)
                    dur = int((time.perf_counter() - t0) * 1000)
                    return ExecResult(
                        stdout="",
                        stderr="timeout",
                        exit_code=None,
                        transport="local",
                        duration_ms=dur,
                        trace_id=tid,
                        command=cmd,
                        error_summary=f"命令执行超时（{timeout:.1f}秒）",
                    )
                rc = proc.returncode
                out = out or ""
                err = err or ""
            dur = int((time.perf_counter() - t0) * 1000)
            out, err, trunc = self._apply_output_budget(out, err)
            rc_int = int(rc) if rc is not None else None
            return ExecResult(
                stdout=out,
                stderr=err,
                exit_code=rc_int,
                transport="local",
                duration_ms=dur,
                trace_id=tid,
                command=cmd,
                truncated=trunc,
                error_summary=None if rc_int == 0 else (err.strip() or f"exit={rc_int}"),
            )
        except subprocess.TimeoutExpired:
            dur = int((time.perf_counter() - t0) * 1000)
            return ExecResult(
                stdout="",
                stderr="timeout",
                exit_code=None,
                transport="local",
                duration_ms=dur,
                trace_id=tid,
                command=cmd,
                error_summary=f"命令执行超时（{timeout:.1f}秒）",
            )
        except Exception as ex:  # pragma: no cover
            dur = int((time.perf_counter() - t0) * 1000)
            return ExecResult(
                stdout="",
                stderr=str(ex),
                exit_code=None,
                transport="local",
                duration_ms=dur,
                trace_id=tid,
                command=cmd,
                error_summary=str(ex),
            )

    def _run_command_streaming(
        self,
        cmd: str,
        timeout: float,
        tid: str,
        t0: float,
        opts: RunOptions,
    ) -> ExecResult:
        cb = opts.stream_chunk
        assert cb is not None
        argv = self._argv_for(cmd)
        posix_kw = posix_start_new_session_kwargs()
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=0,
                shell=False,
                **posix_kw,
            )
        except Exception as ex:  # pragma: no cover
            dur = int((time.perf_counter() - t0) * 1000)
            return ExecResult(
                stdout="",
                stderr=str(ex),
                exit_code=None,
                transport="local",
                duration_ms=dur,
                trace_id=tid,
                command=cmd,
                error_summary=str(ex),
            )

        out_parts: List[str] = []
        err_parts: List[str] = []

        def pump(pipe: Optional[Any], label: str, sink: List[str]) -> None:
            if pipe is None:
                return
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    sink.append(chunk)
                    cb(label, chunk)
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        t_out = threading.Thread(
            target=pump,
            args=(proc.stdout, "stdout", out_parts),
            daemon=True,
        )
        t_err = threading.Thread(
            target=pump,
            args=(proc.stderr, "stderr", err_parts),
            daemon=True,
        )
        t_out.start()
        t_err.start()
        rc: Optional[int] = None
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_tree_sync(proc)
            dur = int((time.perf_counter() - t0) * 1000)
            t_out.join(10)
            t_err.join(10)
            out = "".join(out_parts)
            err = "".join(err_parts)
            if not err.strip():
                err = "timeout"
            out, err, trunc = self._apply_output_budget(out, err)
            return ExecResult(
                stdout=out,
                stderr=err,
                exit_code=None,
                transport="local",
                duration_ms=dur,
                trace_id=tid,
                command=cmd,
                truncated=trunc,
                error_summary=f"命令执行超时（{timeout:.1f}秒）",
            )
        t_out.join()
        t_err.join()
        dur = int((time.perf_counter() - t0) * 1000)
        out = "".join(out_parts)
        err = "".join(err_parts)
        out, err, trunc = self._apply_output_budget(out, err)
        return ExecResult(
            stdout=out,
            stderr=err,
            exit_code=int(rc) if rc is not None else None,
            transport="local",
            duration_ms=dur,
            trace_id=tid,
            command=cmd,
            truncated=trunc,
            error_summary=None if rc == 0 else (err.strip() or f"exit={rc}"),
        )

    def run_command(self, command: str, options: Optional[RunOptions] = None) -> ExecResult:
        opts = options or RunOptions()
        timeout = max(5.0, float(opts.timeout_sec or 120.0))
        tid = uuid.uuid4().hex[:24]
        t0 = time.perf_counter()
        cmd = command or ""
        if opts.stream_chunk is None:
            return self._run_command_blocking(cmd, timeout, tid, t0)
        return self._run_command_streaming(cmd, timeout, tid, t0, opts)

    def close(self) -> None:
        return
