"""Phase 1：Paramiko 非交互单次执行（独立于 PTY Shell）。"""
from __future__ import annotations

import threading
import time
import uuid
from typing import List, Optional

from chibycore.executor_contract import ExecResult, RunOptions

try:
    import paramiko
except ImportError:  # pragma: no cover
    paramiko = None  # type: ignore


class ParamikoSSHOneShotExecutor:
    """单次 exec 信道；每条 RunCommand 可复用底层 TCP 会话。"""

    def __init__(
        self,
        hostname: str,
        port: int,
        username: str,
        password: Optional[str] = None,
        pkey_path: Optional[str] = None,
        pkey_pass: Optional[str] = None,
    ):
        self._hostname = hostname
        self._port = port or 22
        self._username = username
        self._password = password
        self._pkey_path = pkey_path
        self._pkey_pass = pkey_pass
        self._client: Optional["paramiko.SSHClient"] = None

    def connect(self) -> None:
        if paramiko is None:
            raise RuntimeError("paramiko 未安装")
        if self._client is not None:
            return
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = {
            "hostname": self._hostname,
            "port": int(self._port),
            "username": self._username,
            "timeout": 30,
            "banner_timeout": 30,
            "auth_timeout": 30,
        }
        if self._pkey_path:
            kw["key_filename"] = self._pkey_path
            if self._pkey_pass:
                kw["passphrase"] = self._pkey_pass
            if self._password:
                kw["password"] = self._password
        else:
            kw["password"] = self._password
        c.connect(**kw)
        self._client = c

    def run_command(self, command: str, options: Optional[RunOptions] = None) -> ExecResult:
        if self._client is None:
            raise RuntimeError("SSH 未 connect")
        opts = options or RunOptions()
        buf_out: List[str] = []
        buf_err: List[str] = []
        tid = uuid.uuid4().hex[:24]
        t0 = time.perf_counter()
        timeout_sec = max(5.0, float(opts.timeout_sec or 120.0))
        stdin_ch, stdout_ch, stderr_ch = self._client.exec_command(
            command,
            timeout=int(timeout_sec),
        )
        stdin_ch.close()
        try:
            stdout_ch.channel.settimeout(timeout_sec)
            stderr_ch.channel.settimeout(timeout_sec)
        except Exception:
            pass
        cb = opts.stream_chunk
        timed_out = False

        def pump_raw(read_fn, sink: List[str], label: str) -> None:
            nonlocal timed_out
            try:
                while True:
                    if time.perf_counter() - t0 > timeout_sec:
                        timed_out = True
                        break
                    data = read_fn(4096)
                    if not data:
                        break
                    s = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
                    sink.append(s)
                    if cb:
                        cb(label, s)
            except Exception as ex:
                msg = str(ex).lower()
                if "timed out" in msg or "timeout" in msg:
                    timed_out = True
                else:
                    sink.append(str(ex))

        try:
            if cb:
                t_out = threading.Thread(
                    target=pump_raw,
                    args=(stdout_ch.read, buf_out, "stdout"),
                    daemon=True,
                )
                t_err = threading.Thread(
                    target=pump_raw,
                    args=(stderr_ch.read, buf_err, "stderr"),
                    daemon=True,
                )
                t_out.start()
                t_err.start()
                t_out.join(timeout=timeout_sec + 2)
                t_err.join(timeout=2)
                if t_out.is_alive() or t_err.is_alive():
                    timed_out = True
            else:
                pump_raw(stdout_ch.read, buf_out, "stdout")
                pump_raw(stderr_ch.read, buf_err, "stderr")
        except Exception as ex:  # pragma: no cover
            dur = int((time.perf_counter() - t0) * 1000)
            return ExecResult(
                stdout="",
                stderr=str(ex),
                exit_code=None,
                transport="ssh",
                duration_ms=dur,
                trace_id=tid,
                command=command,
            )

        if timed_out:
            try:
                stdout_ch.channel.close()
            except Exception:
                pass
            dur = int((time.perf_counter() - t0) * 1000)
            return ExecResult(
                stdout="".join(buf_out),
                stderr=f"SSH 命令执行超时（>{int(timeout_sec)}s）",
                exit_code=-1,
                transport="ssh",
                duration_ms=dur,
                trace_id=tid,
                command=command,
                meta={"timeout": True, "timeout_sec": timeout_sec},
            )

        try:
            rc = stdout_ch.channel.recv_exit_status()
        except Exception:
            rc = -1
        dur = int((time.perf_counter() - t0) * 1000)
        return ExecResult(
            stdout="".join(buf_out),
            stderr="".join(buf_err),
            exit_code=int(rc),
            transport="ssh",
            duration_ms=dur,
            trace_id=tid,
            command=command,
        )

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
