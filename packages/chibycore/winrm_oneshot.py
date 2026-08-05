"""Phase 1：WinRM 单次 Powershell 调用（独立于交互 Shell）。基于 pypsrp（PSRP Runspace）。"""
from __future__ import annotations

import base64
import re
import time
import uuid
from typing import Any, Callable, List, Optional, Tuple

from chibycore.executor_contract import ExecResult, RunOptions

# 与包装脚本约定的独立一行结尾标记（便于从 execute_ps 聚合输出中解析并剥离）
_PS_EXIT_MARKER_LINE = re.compile(r"(?ms)^__OPS_EXIT_CODE__:(-?\d+)\s*$")


def wrap_powershell_script_for_exit_marker(user_script: str) -> str:
    """本地 ConPTY / 管道注入单行 PowerShell 时使用（与 PSRP 单次执行同一套 Base64 包装）。
    执行后在输出末行写入 ``__OPS_EXIT_CODE__:<int>``，供捕获解析。"""
    return _wrap_ps_for_last_exit_code(user_script)


def parse_ps_exit_marker_codes(text: str) -> List[int]:
    """从终端捕获文本中解析所有 ``__OPS_EXIT_CODE__`` 行（多行注入时可能有多段）。"""
    return [int(m.group(1)) for m in _PS_EXIT_MARKER_LINE.finditer(text or "")]


def strip_ps_exit_marker_lines(text: str) -> str:
    """展示前去掉退出码标记行，避免污染「命令输出」卡片。"""
    if not text:
        return text
    return re.sub(r"(?m)^__OPS_EXIT_CODE__:-?\d+\s*\r?\n?", "", text).rstrip()

try:
    from pypsrp.client import Client  # type: ignore
except ImportError:  # pragma: no cover
    Client = None  # type: ignore

try:
    from pypsrp.complex_objects import PSInvocationState  # type: ignore
    from pypsrp.powershell import DEFAULT_CONFIGURATION_NAME, PowerShell, RunspacePool  # type: ignore
except ImportError:  # pragma: no cover
    PSInvocationState = None  # type: ignore
    DEFAULT_CONFIGURATION_NAME = "Microsoft.PowerShell"  # type: ignore
    PowerShell = None  # type: ignore
    RunspacePool = None  # type: ignore


def _auth_from_transport(transport: str) -> str:
    t = (transport or "ntlm").lower().strip()
    aliases = {
        "ssl": "certificate",
    }
    return aliases.get(t, t)


def _wrap_ps_for_last_exit_code(user_script: str) -> str:
    """
    将用户脚本 UTF-8 做 Base64 嵌套执行，避免引号/换行转义问题；
    在 finally 中输出 __OPS_EXIT_CODE__:<n>，对应远程 $LASTEXITCODE（未设置则 0）。

    输出为**单行**（分号串联）：交互式 PowerShell 对多行脚本会进入续行提示 ``>>``，
    ConPTY/管道逐字符写入时还容易留下未闭合解析态；单行一次提交可避免。

    使用 ``& ([scriptblock]::Create($__raw))`` 代替 ``Invoke-Expression``：部分主机上 IEX 与 PSReadLine
    组合会在**已正常输出后**仍残留次级提示 ``>>``；末尾追加 ``;[void]0`` 再提交一条空效果语句，
    帮助控制台结束当前逻辑输入行（仍只产生一次 Enter）。
    """
    b64 = base64.b64encode(user_script.encode("utf-8")).decode("ascii")
    # 单引号内仅为 Base64 字符集，安全
    return (
        "$ErrorActionPreference='Continue'; "
        f"$__b64='{b64}'; "
        "$__raw=[System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String($__b64)); "
        "$__exit=0; "
        "try { & ([scriptblock]::Create($__raw)); "
        "if ($null -ne $LASTEXITCODE) { try { $__exit=[int]$LASTEXITCODE } catch { $__exit=1 } } } "
        "catch { $__exit=1 } "
        "finally { "
        "if ($__exit -ne 0 -and $Error.Count -gt 0) { try { Write-Output ($Error[0]|Out-String) } catch { } }; "
        "Write-Output ('__OPS_EXIT_CODE__:' + $__exit) "
        "};[void]0"
    )


def _format_psrp_error_record(rec: Any) -> str:
    """pypsrp ErrorRecord 的 __str__ 常为空的 ToString；从字段拆出可读错误正文。"""
    bits: List[str] = []
    for attr in (
        "details_message",
        "message",
        "reason",
        "invocation_position_message",
        "invocation_line",
        "invocation_name",
        "script_stacktrace",
    ):
        try:
            val = getattr(rec, attr, None)
        except Exception:
            val = None
        if val is None:
            continue
        s = str(val).strip()
        if s:
            bits.append(s)
    try:
        ex = getattr(rec, "exception", None)
        if ex is not None:
            es = str(ex).strip()
            if es:
                bits.append(es)
    except Exception:
        pass
    try:
        base = str(rec).strip()
        if base and base not in bits:
            bits.insert(0, base)
    except Exception:
        pass
    seen = set()
    out: List[str] = []
    for b in bits:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return "\n".join(out)


def _aggregate_psrp_stderr(streams: Any) -> str:
    parts: List[str] = []
    for rec in getattr(streams, "error", None) or []:
        line = _format_psrp_error_record(rec).strip()
        if line:
            parts.append(line)
    for w in getattr(streams, "warning", None) or []:
        ws = str(w).strip()
        if ws:
            parts.append(ws)
    return "\n".join(parts)


def _parse_exit_marker_from_stdout(stdout: str) -> Tuple[str, Optional[int]]:
    """从聚合 stdout 中剥离最后一行标记；若无标记则返回原文与 None。"""
    text = stdout or ""
    matches = list(_PS_EXIT_MARKER_LINE.finditer(text))
    if not matches:
        return text, None
    last = matches[-1]
    code = int(last.group(1))
    stripped = text[: last.start()] + text[last.end() :]
    stripped = stripped.rstrip("\r\n")
    return stripped, code


def _ps_output_obj_to_text(obj: Any) -> str:
    """与 Client.execute_ps 中 ``"\\n".join(powershell.output)`` 单元素格式对齐。"""
    if obj is None:
        return ""
    return str(obj)


def _emit_stdout_stream_chunks(cb: Callable[[str, str], None], text: str, *, micro_chunk: int = 192) -> None:
    """
    将一段 stdout 细分为较小块再回调，便于 SSE 侧更接近「逐字/逐小段」观感。
    micro_chunk 过小会增加帧数；192 为折中。
    """
    if not text:
        return
    if len(text) <= micro_chunk:
        cb("stdout", text)
        return
    for i in range(0, len(text), micro_chunk):
        cb("stdout", text[i : i + micro_chunk])


def _run_psrp_poll_stream(
    wsman: Any,
    wrapped_script: str,
    cb: Callable[[str, str], None],
    *,
    timeout_sec: float,
) -> Tuple[str, Any, bool]:
    """
    使用 begin_invoke + poll_receive：管道每产生一条 PIPELINE_OUTPUT 即可本地转发，
    行为与 Client.execute_ps 不同——后者追加 Out-String 会缓冲整段输出。

    使用墙钟 ``timeout_sec``：超时则尝试 ``ps.stop()`` 并抛出 TimeoutError，
    避免阻塞命令挂死。
    """
    if RunspacePool is None or PowerShell is None or PSInvocationState is None:
        raise RuntimeError("pypsrp 未正确安装")

    deadline = time.perf_counter() + max(5.0, float(timeout_sec))
    # 单次 poll 不要超过剩余时间，且不要太长以免取消/超时反应慢
    sleep_s = 0.05

    with RunspacePool(wsman, configuration_name=DEFAULT_CONFIGURATION_NAME) as pool:
        ps = PowerShell(pool)
        ps.add_cmdlet("Invoke-Expression").add_parameter("Command", wrapped_script)
        ps.begin_invoke()
        out_i = 0
        err_i = 0
        timed_out = False
        while ps.state == PSInvocationState.RUNNING:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                timed_out = True
                break
            poll_ms = max(500, min(5000, int(remaining * 1000)))
            ps.poll_invoke(timeout=poll_ms)
            while len(ps.output) > out_i:
                if out_i > 0:
                    cb("stdout", "\n")
                piece = ps.output[out_i]
                txt = _ps_output_obj_to_text(piece)
                _emit_stdout_stream_chunks(cb, txt)
                out_i += 1
            while len(ps.streams.error) > err_i:
                rec = ps.streams.error[err_i]
                line = _format_psrp_error_record(rec).strip() or str(rec)
                cb("stderr", line + "\n")
                err_i += 1
            time.sleep(sleep_s)

        if timed_out:
            try:
                ps.stop()
            except Exception:
                pass
            raise TimeoutError(f"WinRM 命令执行超时（>{int(timeout_sec)}s）")

        ps.end_invoke()

        while len(ps.output) > out_i:
            if out_i > 0:
                cb("stdout", "\n")
            txt = _ps_output_obj_to_text(ps.output[out_i])
            _emit_stdout_stream_chunks(cb, txt)
            out_i += 1
        while len(ps.streams.error) > err_i:
            rec = ps.streams.error[err_i]
            line = _format_psrp_error_record(rec).strip() or str(rec)
            cb("stderr", line + "\n")
            err_i += 1

        raw_joined = "\n".join(_ps_output_obj_to_text(x) for x in ps.output)
        return raw_joined, ps.streams, ps.had_errors


def _exec_result_from_strings(
    command: str,
    tid: str,
    t0: float,
    stdout_raw: str,
    streams: Any,
    had_errors: bool,
) -> ExecResult:
    stderr = _aggregate_psrp_stderr(streams)
    if had_errors and not stderr.strip():
        stderr = (
            "PowerShell 报告错误（had_errors），但 PSRP 错误流未解析出可读文本。"
            " 若仅为 LASTEXITCODE<>0，请查看上行命令输出。"
        )
    stdout_clean, parsed_code = _parse_exit_marker_from_stdout(str(stdout_raw or ""))
    if parsed_code is not None:
        code = parsed_code
    else:
        code = 1 if had_errors else 0
    dur = int((time.perf_counter() - t0) * 1000)
    return ExecResult(
        stdout=stdout_clean,
        stderr=stderr,
        exit_code=code,
        transport="winrm",
        duration_ms=dur,
        trace_id=tid,
        command=command,
    )


class WinRMOneShotExecutor:
    """使用 pypsrp Client（WS-Man + PSRP）执行远程 PowerShell 脚本。"""

    def __init__(
        self,
        server: str,
        port: int,
        username: str,
        password: str,
        ssl: bool = False,
        transport: str = "ntlm",
        server_cert_validation: str = "ignore",
    ):
        self._server = server
        self._port = int(port)
        self._username = username
        self._password = password
        self._ssl = bool(ssl)
        self._auth = _auth_from_transport(transport)
        self._cert_validation = server_cert_validation == "validate"
        self._client = None

    def connect(self) -> None:
        if Client is None:
            raise RuntimeError("pypsrp 未安装")
        if self._client is not None:
            return
        self._client = Client(
            server=self._server,
            port=self._port,
            ssl=self._ssl,
            username=self._username,
            password=self._password,
            auth=self._auth,
            cert_validation=self._cert_validation,
        )

    def run_command(self, command: str, options: Optional[RunOptions] = None) -> ExecResult:
        if self._client is None:
            raise RuntimeError("WinRM 未 connect")
        opts = options or RunOptions()
        tid = uuid.uuid4().hex[:24]
        t0 = time.perf_counter()
        wrapped = _wrap_ps_for_last_exit_code(command)
        timeout_sec = max(5.0, float(opts.timeout_sec or 120.0))

        # 统一走 poll 路径以强制墙钟超时；无 stream_chunk 时静默收集
        chunks: List[str] = []
        user_cb = opts.stream_chunk

        def _cb(stream: str, text: str) -> None:
            if stream == "stdout" and text:
                chunks.append(text)
            if user_cb is not None:
                user_cb(stream, text)

        try:
            raw_joined, streams, had_errors = _run_psrp_poll_stream(
                self._client.wsman,
                wrapped,
                _cb,
                timeout_sec=timeout_sec,
            )
        except TimeoutError as exc:
            dur = int((time.perf_counter() - t0) * 1000)
            return ExecResult(
                stdout="".join(chunks),
                stderr=str(exc),
                exit_code=-1,
                transport="winrm",
                duration_ms=dur,
                trace_id=tid,
                command=command,
                meta={"timeout": True, "timeout_sec": timeout_sec},
            )
        # prefer joined from poll; chunks may micro-split
        raw = raw_joined if raw_joined else "".join(chunks)
        return _exec_result_from_strings(command, tid, t0, raw, streams, had_errors)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
