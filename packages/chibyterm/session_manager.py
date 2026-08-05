"""多会话管理器 — 管理所有终端会话的生命周期。"""
from __future__ import annotations

import asyncio
import codecs
import inspect
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from pypsrp.shell import CommandState, Process, WinRS
from pypsrp.wsman import NAMESPACES, OptionSet, WSMan

from .models import ConnType, SessionStatus, TerminalSession
from .plan_state import PlanRuntime
from chibycore.output_budget import TERMINAL_CAPTURE_RING_MAX_CHARS
from chibycore.winrm_oneshot import WinRMOneShotExecutor, wrap_powershell_script_for_exit_marker

logger = logging.getLogger(__name__)

_RE_WIN_DRIVE = re.compile(r"^[A-Za-z]:$")

# WinRM worker 线程收到此对象后退出循环并清理连接（仅由 close 入队）
_WINRM_WORKER_QUIT = object()


def _normalize_win_conpty_input(data: str) -> str:
    """本地 Windows ConPTY + PowerShell/cmd 行结束规范化。

    1) 单独 LF：往往不能可靠「提交行」，PS 易进入续行 ``>>`` → 未成对 ``\\n`` 补成 CRLF。
    2) 仅 CR：xterm Enter 常见只发 ``\\r``；在 ConPTY + PSReadLine 下有时不能结束当前逻辑行，
       表现为 ``pwd`` 后先刷出 ``>>`` 再出表格 → 将 ``\\r`` 且其后非 ``\\n`` 的断行补成 CRLF。

    注意：含 ``\\r`` 且无 ``\\n`` 的粘贴块可能被改写（极少见于日常 shell 输入）。"""
    if not data:
        return data
    t = re.sub(r"(?<!\r)\n", "\r\n", data)
    t = re.sub(r"\r(?!\n)", "\r\n", t)
    return t


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except ValueError:
        return default


def _env_str(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v)


def _env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _normalize_winrs_encoding(encoding: str) -> str:
    """WinRS 控制台 stdin/stdout 共用编码名（默认 gbk / CP936）。"""
    enc = (encoding or "gbk").strip().lower()
    if enc in ("auto", ""):
        enc = "gbk"
    aliases = {"cp936": "gbk", "gb2312": "gbk", "windows-936": "gbk", "utf8": "utf-8"}
    return aliases.get(enc, enc)


def _encode_winrs_stdin(text: str, encoding: str) -> bytes:
    """浏览器/WebSocket 为 Unicode；WinRS 控制台 stdin 需与 OPS_WINRM_WINRS_ENCODING 一致。"""
    enc = _normalize_winrs_encoding(encoding)
    try:
        return text.encode(enc, errors="replace")
    except LookupError:
        logger.warning("未知 WinRS stdin 编码 %s，回退 utf-8", encoding)
        return text.encode("utf-8", errors="replace")


def _winrs_stdio_incremental_decoder(encoding: str):
    """
    WinRS 控制台字节流解码器（支持分包边界上的多字节字符）。
    中文 Windows 控制台默认多为 GBK（CP936）；误用 UTF-8 会把「目录」解成「Ŀ¼」。
    utf-8：需远端 chcp 65001 / UTF-8 控制台，并设 OPS_WINRM_WINRS_ENCODING=utf-8。
    """
    enc = _normalize_winrs_encoding(encoding)
    try:
        return codecs.getincrementaldecoder(enc)(errors="replace")
    except LookupError:
        logger.warning("未知 OPS_WINRM_WINRS_ENCODING=%s，回退 utf-8", encoding)
        return codecs.getincrementaldecoder("utf-8")(errors="replace")


def _pypsrp_auth_from_transport(transport: str) -> str:
    """与主机配置里的 winrm_transport 对齐到 pypsrp WSMan auth。"""
    t = (transport or "ntlm").lower().strip()
    if t == "ssl":
        return "certificate"
    return t


def _winrm_error_hints(detail: str) -> str:
    """在 WinRM 失败信息末尾追加简短排查说明（中文）。"""
    if not detail:
        return ""
    lines = []
    low = detail.lower()
    if "127.0.0.1" in detail or "localhost" in low:
        lines.append(
            "提示：127.0.0.1 表示连本机。仅当「本机就是目标 Windows 且已启用 WinRM」时才正确；"
            "连远程请改为该机器的局域网 IP 或主机名。"
        )
    if "10061" in detail or "积极拒绝" in detail or "connection refused" in low or "failed to establish" in low:
        lines.append(
            "提示：目标未接受连接。请在「目标 Windows」上以管理员执行：Enable-PSRemoting -Force；"
            "检查防火墙是否放行 5985（HTTP）或 5986（HTTPS）；本机测试可运行：winrm id"
        )
    if "mic" in low or "message integrity" in low or "spnego" in low:
        lines.append(
            "提示：MIC/Spnego 多与 NTLM 会话并发读写有关（已做协议串行化）；若仍出现，"
            "请在主机配置将 WinRM 传输改为 credssp，或核对账号密码与域（DOMAIN\\\\user）。"
        )
    if not lines:
        return ""
    return "\r\n\r\n" + "\r\n".join(lines)


class _ConsoleWinRS(WinRS):
    """WinRS.command 附加 WINRS_CONSOLEMODE_STDIN（与原 pywinrm console_mode_stdin 一致）。"""

    def command(
        self,
        executable: str,
        arguments=None,
        no_shell: bool = False,
        command_id=None,
    ):
        rsp = NAMESPACES["rsp"]
        options = OptionSet()
        options.add_option("WINRS_SKIP_CMD_SHELL", str(no_shell))
        options.add_option("WINRS_CONSOLEMODE_STDIN", "TRUE")
        arguments = arguments if arguments is not None else []
        cmd = ET.Element("{%s}CommandLine" % rsp)
        if command_id is not None:
            cmd.attrib["CommandId"] = command_id
        ET.SubElement(cmd, "{%s}Command" % rsp).text = executable
        for argument in arguments:
            ET.SubElement(cmd, "{%s}Arguments" % rsp).text = argument
        return self.wsman.command(
            self.resource_uri, cmd, option_set=options, selector_set=self._selector_set
        )


class _FixedOpTimeoutWSMan(WSMan):
    """
    pypsrp 的 ``WSMan._create_header`` 使用 ``timeout or self.operation_timeout``，
    使得 ``timeout=0``（协议里的 PT0S，Receive 无输出时立即返回）被当成「未传」而退回默认，
    交互 Shell 主循环里的 ``poll_invoke(timeout=0)`` 长期卡在约 1s 的 OperationTimeout 上，键盘极不跟手。
    此处临时覆写 ``operation_timeout``，使 ``timeout=0`` 能生成 ``PT0S``。
    """

    def _create_header(
        self,
        action: str,
        resource_uri: str,
        option_set=None,
        selector_set=None,
        timeout=None,
    ):
        effective = self.operation_timeout if timeout is None else timeout
        saved = self.operation_timeout
        self.operation_timeout = effective
        try:
            return super()._create_header(
                action, resource_uri, option_set, selector_set, None
            )
        finally:
            self.operation_timeout = saved


# ─── SSH Shell 会话（paramiko）────────────────────────────────────────────────

class SSHShellProcess:
    """通过 paramiko 建立 SSH 会话，支持 PTY shell。"""

    def __init__(self, host: str, port: int, username: str,
                 password: Optional[str] = None,
                 width: int = 80, height: int = 24,
                 pkey_path: Optional[str] = None,
                 pkey_passphrase: Optional[str] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.pkey_path = (pkey_path or "").strip() or None
        self.pkey_passphrase = pkey_passphrase
        self.width = width
        self.height = height
        self._client = None
        self._channel = None
        self._reader_task: Optional[asyncio.Task] = None
        self._output_callback: Optional[callable] = None
        self._closed = False
        self._q: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._connect_error: Optional[str] = None

    def set_output_callback(self, cb: callable):
        self._output_callback = cb

    async def start(self) -> bool:
        """启动 SSH 连接，在后台线程中执行。返回 True 表示成功。"""
        def _ssh_connect():
            try:
                import paramiko
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                kw = {
                    "hostname": self.host,
                    "port": self.port,
                    "username": self.username,
                    "timeout": 15,
                }
                if self.password:
                    kw["password"] = self.password
                if self.pkey_path:
                    import os as _os

                    kpath = _os.path.expanduser(self.pkey_path)
                    pkey = None
                    for KeyCls in (
                        getattr(paramiko, "RSAKey", None),
                        getattr(paramiko, "Ed25519Key", None),
                        getattr(paramiko, "ECDSAKey", None),
                    ):
                        if KeyCls is None:
                            continue
                        try:
                            pkey = KeyCls.from_private_key_file(
                                kpath,
                                password=(self.pkey_passphrase or None),
                            )
                            break
                        except Exception:
                            continue
                    if pkey is None:
                        raise ValueError(f"无法加载私钥: {kpath}")
                    kw["pkey"] = pkey
                elif not self.password:
                    raise ValueError("SSH 需要 password 或 ssh_private_key_path")
                client.connect(**kw)
                # 空闲会话：定期发 SSH 级 keepalive，避免 NAT/防火墙/sshd 掐断（WinError 10054）
                try:
                    transport = client.get_transport()
                    if transport is not None:
                        transport.set_keepalive(30)
                except Exception:
                    logger.debug("SSH set_keepalive failed", exc_info=True)
                channel = client.invoke_shell(
                    term="xterm-256color",
                    width=self.width, height=self.height,
                )
                channel.settimeout(0.05)
                self._client = client
                self._channel = channel

                while not self._closed:
                    try:
                        if channel.recv_ready():
                            data = channel.recv(4096)
                            if data:
                                self._q.put(data.decode("utf-8", errors="replace"))
                        # 远端已关闭 / 通道退出
                        if channel.exit_status_ready() or channel.closed:
                            break
                        time.sleep(0.05)
                    except Exception:
                        if self._closed:
                            break
                        time.sleep(0.05)

                channel.close()
                client.close()
            except Exception as e:
                logger.error(f"SSH thread error: {e}")
                self._connect_error = str(e)
                self._q.put(f"\r\n[SSH 连接错误: {e}]\r\n")
                self._closed = True

        self._thread = threading.Thread(target=_ssh_connect, daemon=True)
        self._thread.start()

        # 等待连接建立（最多 15s）
        start = asyncio.get_event_loop().time()
        loop = asyncio.get_event_loop()
        while self._channel is None and self._connect_error is None:
            await asyncio.sleep(0.2)
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed > 15:
                self._closed = True
                self._connect_error = "连接超时"
                return False
            if self._closed:
                return False

        if self._connect_error:
            return False

        # 启动读取队列协程
        self._reader_task = asyncio.create_task(self._read_queue())
        return True

    async def _read_queue(self):
        try:
            while not self._closed:
                await asyncio.sleep(0.05)
                try:
                    while True:
                        data = self._q.get_nowait()
                        if not data:
                            self._closed = True
                            break
                        if self._output_callback:
                            if asyncio.iscoroutinefunction(self._output_callback):
                                await self._output_callback(data)
                            else:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, lambda cb=self._output_callback, d=data: cb(d)
                                )
                except queue.Empty:
                    if self._closed:
                        break
                    continue
        except Exception as e:
            logger.debug(f"SSH queue read done: {e}")
        finally:
            self._closed = True

    async def write(self, data: str) -> bool:
        """发送输入到 SSH channel。"""
        if self._closed:
            return False
        if not self._channel:
            return False
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, lambda: self._channel.send(data))
            return True
        except Exception as e:
            logger.error(f"SSH write: {e}")
            return False

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height
        if self._channel:
            try:
                self._channel.resize_pty(width=width, height=height)
            except Exception as e:
                logger.error(f"SSH resize: {e}")

    async def close(self):
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        ch = self._channel
        if not ch:
            return False
        try:
            return not ch.closed
        except Exception:
            return False


# ─── WinRM + PowerShell 远程会话（pypsrp / WinRS）─────────────────────────────

class WinRMShellProcess:
    """通过 WinRM 在 Windows 上启动交互式 PowerShell，stdin/stdout 经 WS-Man 流式传输。

    设计要点：
    - **仅 worker 线程** 调用 pypsrp ``WSMan`` / ``WinRS`` / ``Process``（避免 NTLM 等会话跨线程交错）。
    - 异步 ``write()`` 只把字节放入 ``_stdin_queue``；worker 在每次 Receive **之前**先排空队列，
      避免长时间阻塞导致键盘与输出卡顿。
    - ``WSMan(operation_timeout=…)`` 取较小值，使无输出时 Receive 尽快返回，便于穿插发送 stdin。
    """

    # 非 Receive 请求默认 OperationTimeout（秒）。Receive 单独用 poll_invoke(timeout=…) 覆盖，默认 0 → PT0S。
    WINRM_OPERATION_TIMEOUT_SEC = 1
    # 一次 send 前合并 stdin 的额外等待（秒），把连击/粘贴前几字节打成一包
    WINRM_STDIN_COALESCE_SEC = 0.02

    def __init__(
        self,
        host: str,
        winrm_port: int,
        username: str,
        password: Optional[str] = None,
        use_ssl: bool = False,
        transport: str = "ntlm",
        server_cert_validation: str = "ignore",
        width: int = 80,
        height: int = 24,
    ):
        self.host = host
        self.winrm_port = winrm_port
        self.username = username
        self.password = password or ""
        self.use_ssl = use_ssl
        self.transport = transport
        self.server_cert_validation = server_cert_validation if server_cert_validation in ("validate", "ignore") else "ignore"
        self.width = width
        self.height = height
        self._command_id: Optional[str] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._output_callback: Optional[callable] = None
        self._closed = False
        self._closing = False
        self._q: queue.Queue = queue.Queue()
        self._stdin_queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._connect_error: Optional[str] = None
        # worker 内赋值为 OPS_WINRM_READ_TIMEOUT_SEC，供 close 时 join 上限
        self._winrm_http_read_timeout: int = 12
        self._winrs_encoding = _normalize_winrs_encoding(
            _env_str("OPS_WINRM_WINRS_ENCODING", "gbk")
        )

    def set_output_callback(self, cb: callable):
        self._output_callback = cb

    def _drain_stdin_coalesced(self) -> tuple[Optional[bytes], bool]:
        """从 stdin 队列取出数据；遇 QUIT 则第二项为 True。连击/多段时再短等合并，单字符零额外延迟。"""
        buf = bytearray()
        n_first = 0
        while True:
            try:
                item = self._stdin_queue.get_nowait()
            except queue.Empty:
                break
            if item is _WINRM_WORKER_QUIT:
                return (bytes(buf) if buf else None, True)
            buf.extend(item)
            n_first += 1
        if not buf:
            return (None, False)
        # 仅当本轮已有多段（连击/粘贴）时，再稍等合并后续片段；单键不人为加延迟
        if n_first < 2:
            return (bytes(buf), False)
        deadline = time.monotonic() + self.WINRM_STDIN_COALESCE_SEC
        while time.monotonic() < deadline:
            try:
                item = self._stdin_queue.get_nowait()
            except queue.Empty:
                time.sleep(0.0015)
                continue
            if item is _WINRM_WORKER_QUIT:
                return (bytes(buf), True)
            buf.extend(item)
        return (bytes(buf), False)

    def _winrm_worker(self):
        shell: Optional[_ConsoleWinRS] = None
        proc: Optional[Process] = None
        wsman: Optional[WSMan] = None
        try:
            from pypsrp.exceptions import WSManFaultError

            read_to = _env_int("OPS_WINRM_READ_TIMEOUT_SEC", 12)
            op_to = self.WINRM_OPERATION_TIMEOUT_SEC
            if read_to <= op_to:
                read_to = op_to + 2
            # 传给 Process.poll_invoke → Receive 的 OperationTimeout（秒）。0=PT0S，跟手最好；不稳定可设 1。
            recv_poll = max(0, _env_int("OPS_WINRM_RECEIVE_POLL_SEC", 0))

            auth = _pypsrp_auth_from_transport(self.transport)
            cert_val = self.server_cert_validation == "validate"

            wsman = _FixedOpTimeoutWSMan(
                server=self.host,
                port=self.winrm_port,
                ssl=self.use_ssl,
                username=self.username,
                password=self.password,
                auth=auth,
                cert_validation=cert_val,
                operation_timeout=op_to,
                read_timeout=read_to,
            )
            self._winrm_http_read_timeout = read_to
            logger.info(
                "WinRM(pypsrp): read_timeout=%ss default_op=%ss recv_Receive_PT=%ss "
                "(OPS_WINRM_READ_TIMEOUT_SEC；OPS_WINRM_RECEIVE_POLL_SEC 为 Receive 专用，0=PT0S)",
                read_to,
                op_to,
                recv_poll,
            )

            shell = _ConsoleWinRS(wsman)
            shell.open()
            proc = Process(shell, "powershell.exe", ["-NoLogo"], no_shell=True)
            proc.begin_invoke()
            self._command_id = proc.id

            # 默认 gbk：中文 Windows 控制台 historically CP936；若乱码像「UTF-8 当 GBK 解」或表头中文全错，
            # 请设 OPS_WINRM_WINRS_ENCODING=utf-8（远端需 UTF-8 控制台 / chcp 65001，勿与 GBK 解码混用）。
            dec_enc = _env_str("OPS_WINRM_WINRS_ENCODING", "gbk")
            dec_out = _winrs_stdio_incremental_decoder(dec_enc)
            dec_err = _winrs_stdio_incremental_decoder(dec_enc)
            dn = _normalize_winrs_encoding(dec_enc)
            decode_as_utf8 = dn == "utf-8"
            # 默认按 GBK 解码时切勿自动 chcp 65001：否则远端改为 UTF-8 字节流，与 GBK 解码器冲突会更乱。
            if not _env_int("OPS_WINRM_WINRS_SKIP_UTF8_INIT", 0):
                raw_init = os.environ.get("OPS_WINRM_WINRS_INIT_CMD")
                if raw_init is None:
                    init_ps = (
                        "try { chcp 65001 | Out-Null } catch {}; "
                        "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}; "
                        "try { [Console]::InputEncoding = [System.Text.Encoding]::UTF8 } catch {}"
                    )
                    if not decode_as_utf8:
                        init_ps = ""
                else:
                    init_ps = raw_init.strip()
                if init_ps:
                    try:
                        proc.send(_encode_winrs_stdin(init_ps + "\r\n", "utf-8"), end=False)
                    except Exception as e:
                        logger.debug("WinRM WinRS 会话控制台初始化未发送（可忽略）: %s", e)
            logger.info(
                "WinRM WinRS 输出解码: %s（英文机且已 UTF-8 控制台可设 utf-8；"
                "关闭 chcp 注入设 OPS_WINRM_WINRS_SKIP_UTF8_INIT=1）",
                dec_enc,
            )

            prev_out = 0
            prev_err = 0

            while not self._closed:
                if not wsman or not shell or not proc or not proc.id:
                    break

                merged, should_quit = self._drain_stdin_coalesced()
                if merged:
                    try:
                        proc.send(merged, end=False)
                    except Exception as e:
                        logger.error(f"WinRM 写入: {e}")
                        self._q.put(f"\r\n[WinRM 错误: {e}]\r\n")
                        self._q.put("")
                        self._closed = True
                        break
                if should_quit:
                    self._closed = True
                    break
                if self._stdin_queue.qsize() > 0:
                    continue

                try:
                    proc.poll_invoke(timeout=recv_poll)
                except WSManFaultError as exc:
                    if exc.code != 2150858793:
                        logger.error(f"WinRM 读取出错: {exc}")
                        self._q.put(f"\r\n[WinRM 错误: {exc}]\r\n")
                        self._q.put("")
                        break
                    time.sleep(0.001)
                    continue
                except Exception as e:
                    logger.error(f"WinRM 读取出错: {e}")
                    self._q.put(f"\r\n[WinRM 错误: {e}]\r\n")
                    self._q.put("")
                    break

                new_out = proc.stdout[prev_out:]
                new_err = proc.stderr[prev_err:]
                prev_out = len(proc.stdout)
                prev_err = len(proc.stderr)
                if new_out:
                    self._q.put(dec_out.decode(new_out))
                if new_err:
                    self._q.put(dec_err.decode(new_err))
                if proc.state == CommandState.DONE:
                    try:
                        t_o = dec_out.decode(b"", final=True)
                        t_e = dec_err.decode(b"", final=True)
                        if t_o:
                            self._q.put(t_o)
                        if t_e:
                            self._q.put(t_e)
                    except Exception:
                        pass
                    self._q.put("\r\n[PowerShell 已退出]\r\n")
                    self._q.put("")
                    break
        except Exception as e:
            logger.error(f"WinRM 线程异常: {e}")
            self._connect_error = str(e)
            self._q.put(f"\r\n[WinRM 连接错误: {e}]\r\n")
            self._q.put("")
        finally:
            self._closed = True
            try:
                if proc and proc.id:
                    try:
                        proc.send(b"", end=True)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                if shell and getattr(shell, "opened", False):
                    shell.close()
            except Exception:
                pass
            try:
                if wsman:
                    wsman.close()
            except Exception:
                pass

    async def start(self) -> bool:
        self._thread = threading.Thread(target=self._winrm_worker, daemon=True)
        self._thread.start()

        t0 = asyncio.get_event_loop().time()
        while self._command_id is None and self._connect_error is None and not self._closed:
            await asyncio.sleep(0.1)
            if asyncio.get_event_loop().time() - t0 > 30:
                self._connect_error = "WinRM 连接超时"
                self._closed = True
                return False

        if self._connect_error or not self._command_id:
            # 连接阶段失败时 worker 可能已往队列写入错误信息，需 drain 才能显示到 xterm
            for _ in range(40):
                try:
                    while True:
                        data = self._q.get_nowait()
                        if data and data != "" and self._output_callback:
                            if asyncio.iscoroutinefunction(self._output_callback):
                                await self._output_callback(data)
                            else:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, lambda d=data: self._output_callback(d)
                                )
                except queue.Empty:
                    pass
                await asyncio.sleep(0.05)
            return False

        self._reader_task = asyncio.create_task(self._read_queue())
        return True

    async def _read_queue(self):
        try:
            while not self._closed:
                await asyncio.sleep(0.006)
                try:
                    while True:
                        data = self._q.get_nowait()
                        if data == "":
                            self._closed = True
                            break
                        if self._output_callback:
                            if asyncio.iscoroutinefunction(self._output_callback):
                                await self._output_callback(data)
                            else:
                                await asyncio.get_event_loop().run_in_executor(
                                    None, lambda cb=self._output_callback, d=data: cb(d)
                                )
                except queue.Empty:
                    if self._closed:
                        break
                    continue
        except Exception as e:
            logger.debug(f"WinRM queue read结束: {e}")
        finally:
            self._closed = True

    async def write(self, data: str) -> bool:
        if self._closed or self._closing or self._command_id is None:
            return False
        # 浏览器/xterm 常见只发 \n，PowerShell 交互期望 \r\n
        if data == "\r":
            to_send = "\r\n"
        else:
            to_send = data.replace("\r\n", "\n").replace("\n", "\r\n")
        payload = _encode_winrs_stdin(to_send, self._winrs_encoding)
        try:
            self._stdin_queue.put_nowait(payload)
            return True
        except Exception as e:
            logger.error(f"WinRM 入队写入: {e}")
            return False

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height

    async def close(self):
        self._closing = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        try:
            self._stdin_queue.put_nowait(_WINRM_WORKER_QUIT)
        except Exception:
            pass
        if self._thread:
            self._thread.join(timeout=max(45, self._winrm_http_read_timeout + 15))
        self._closed = True

    def is_alive(self) -> bool:
        return not self._closed and self._command_id is not None


# ─── WinRM：PSRP 按行（与同事 Demo 类似，无 WinRS 长连接）──────────────────────

_CD_LINE = re.compile(r"^cd(?:\s+(.*))?\s*$", re.I)


class WinRMPSRPLineShellProcess:
    """
    使用 ``WinRMOneShotExecutor``（PSRP ``execute_ps``）在每次换行时执行整行 PowerShell。
    无 WinRS 流式 Shell，无 PSReadLine；适合「按命令执行」、低延迟，不适合 vim 等全屏 TUI。
    """

    def __init__(
        self,
        host: str,
        winrm_port: int,
        username: str,
        password: Optional[str] = None,
        use_ssl: bool = False,
        transport: str = "ntlm",
        server_cert_validation: str = "ignore",
        width: int = 80,
        height: int = 24,
    ):
        self.host = host
        self.winrm_port = winrm_port
        self.username = username
        self.password = password or ""
        self.use_ssl = use_ssl
        self.transport = transport
        self.server_cert_validation = (
            server_cert_validation
            if server_cert_validation in ("validate", "ignore")
            else "ignore"
        )
        self.width = width
        self.height = height
        self._executor: Optional[WinRMOneShotExecutor] = None
        self._buf = ""
        self._cwd = "C:\\"
        self._output_callback: Optional[callable] = None
        self._closed = False
        self._connect_error: Optional[str] = None
        # 仅当 shell_input(..., echo_psrp_line=True) 注入时累计，供 llm_command_result / 命令集等取真实退出码
        self._inject_batch_any_fail = False
        self._inject_batch_last_exit_code: Optional[int] = None
        self._inject_batch_ran_remote_line = False

    def reset_psrp_inject_batch(self) -> None:
        self._inject_batch_any_fail = False
        self._inject_batch_last_exit_code = None
        self._inject_batch_ran_remote_line = False

    def _psrp_note_line_outcome(self, *, failed: bool, exit_code: Optional[int]) -> None:
        self._inject_batch_ran_remote_line = True
        if failed:
            self._inject_batch_any_fail = True
        self._inject_batch_last_exit_code = exit_code

    def consume_psrp_inject_batch_outcome(self) -> Optional[Tuple[str, Optional[int]]]:
        """若本批次至少执行过一行远程逻辑，返回 (pass|fail, exit_code) 并重置累计器。"""
        if not self._inject_batch_ran_remote_line:
            return None
        st = "fail" if self._inject_batch_any_fail else "pass"
        code = self._inject_batch_last_exit_code
        self.reset_psrp_inject_batch()
        return (st, code)

    def set_output_callback(self, cb: callable):
        self._output_callback = cb

    @staticmethod
    def _sanitize_psrp_display(text: str) -> str:
        """
        xterm.js 把单独的 \\r 当成「回行首」，远程 Format-Table / 进度条里的 CR 会把表格打成碎片。
        统一成 \\n，并去掉 CLIXML / Progress 常见噪声行。
        """
        if not text:
            return ""
        t = text.replace("\r\n", "\n").replace("\r", "\n")
        lines_out = []
        for line in t.split("\n"):
            st = line.strip()
            if st.startswith("#< CLIXML"):
                continue
            if st.startswith("<Objs ") and "xmlns=" in st:
                continue
            if "PSDataStreams object at 0x" in line:
                continue
            lines_out.append(line)
        return "\n".join(lines_out)

    @staticmethod
    def _escape_ps_literal_path(path: str) -> str:
        return path.replace("'", "''")

    @staticmethod
    def _join_cd(cwd: str, arg: str) -> str:
        arg = arg.strip().strip('"').strip("'")
        if arg == "..":
            parts = [p for p in cwd.split("\\") if p]
            if len(parts) <= 1:
                if len(parts) == 1 and _RE_WIN_DRIVE.match(parts[0] or ""):
                    return parts[0] + "\\"
                return cwd
            return "\\".join(parts[:-1])
        if len(arg) >= 2 and arg[1] == ":":
            return arg
        if arg.startswith("\\\\"):
            return arg
        base = cwd.rstrip("\\")
        if not base:
            return arg
        return base + "\\" + arg

    async def _emit(self, text: str):
        text = self._sanitize_psrp_display(text)
        if not text or not self._output_callback:
            return
        if asyncio.iscoroutinefunction(self._output_callback):
            await self._output_callback(text)
        else:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._output_callback(text)
            )

    async def _run_ps(self, script: str):
        assert self._executor is not None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda s=script: self._executor.run_command(s)
        )

    async def _path_exists(self, path: str) -> bool:
        ep = self._escape_ps_literal_path(path)
        r = await self._run_ps(f"Test-Path -LiteralPath '{ep}'")
        t = (r.stdout or "").strip().lower()
        return t == "true" or t.endswith("true")

    async def _execute_line(
        self,
        raw_line: str,
        *,
        psrp_echo_line: Optional[str] = None,
        track_outcome: bool = False,
    ):
        if self._closed or not self._executor:
            return
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        # 与 inject echo 同一原子串发出（一次 WebSocket），避免前端在两条 output 之间 redraw 提示符插一行孤立的 PS …>
        echo_prefix = ""
        if psrp_echo_line:
            echo_prefix = "PS " + self._cwd + "> " + psrp_echo_line + "\n"

        if not stripped:
            await self._emit(echo_prefix + "\n")
            return

        m = _CD_LINE.match(stripped)
        if m:
            arg = (m.group(1) or "").strip()
            if not arg:
                await self._emit(echo_prefix + self._cwd + "\n")
                if track_outcome:
                    self._psrp_note_line_outcome(failed=False, exit_code=0)
                return
            new_path = self._join_cd(self._cwd, arg)
            ok = await self._path_exists(new_path)
            if ok:
                self._cwd = new_path
                await self._emit(echo_prefix + "\n")
                if track_outcome:
                    self._psrp_note_line_outcome(failed=False, exit_code=0)
            else:
                await self._emit(
                    echo_prefix
                    + f"Set-Location : Cannot find path '{new_path}' "
                    "because it does not exist.\n"
                )
                if track_outcome:
                    self._psrp_note_line_outcome(failed=True, exit_code=1)
            return

        cwd_lit = self._escape_ps_literal_path(self._cwd)
        ps = (
            "$ProgressPreference='SilentlyContinue'; "
            "$InformationPreference='SilentlyContinue'; "
            "Set-Location -LiteralPath '{0}'; {1}".format(cwd_lit, line)
        )
        try:
            result = await self._run_ps(ps)
        except Exception as e:
            logger.error("WinRM PSRP 按行执行失败: %s", e)
            await self._emit(echo_prefix + f"[执行错误: {e}]\n")
            if track_outcome:
                self._psrp_note_line_outcome(failed=True, exit_code=None)
            return

        out = (result.stdout or "").rstrip()
        err = (result.stderr or "").rstrip()
        pieces = []
        if out:
            pieces.append(out)
        if err:
            pieces.append(err)
        body = "\n".join(pieces)
        if body:
            await self._emit(echo_prefix + body + "\n")
        else:
            await self._emit(echo_prefix + "\n")

        if track_outcome:
            ec = result.exit_code
            failed = ec is None or ec != 0
            self._psrp_note_line_outcome(failed=failed, exit_code=ec)

    async def start(self) -> bool:
        loop = asyncio.get_event_loop()

        def boot() -> WinRMOneShotExecutor:
            ex = WinRMOneShotExecutor(
                server=self.host,
                port=self.winrm_port,
                username=self.username,
                password=self.password,
                ssl=self.use_ssl,
                transport=self.transport,
                server_cert_validation=self.server_cert_validation,
            )
            ex.connect()
            ex.run_command("$true")
            return ex

        try:
            self._executor = await loop.run_in_executor(None, boot)
        except Exception as e:
            logger.error("WinRM PSRP 按行模式建连失败: %s", e)
            self._connect_error = str(e)
            return False

        await self._emit(
            "[PSRP 按行模式] 与同事 Demo 一致：输入由浏览器本地编辑，Enter 提交一行。"
            " 列表建议：dir | Out-String -Width 220 或 Get-ChildItem | Format-List\n"
        )
        return True

    async def write(
        self, data: str, *, echo_psrp_line: bool = False, track_inject_outcome: bool = False
    ) -> bool:
        if self._closed or not self._executor:
            return False
        chunk = data.replace("\r\n", "\n").replace("\r", "\n")
        for ch in chunk:
            if ch == "\n":
                echo_arg = self._buf if echo_psrp_line and self._buf != "" else None
                await self._execute_line(
                    self._buf,
                    psrp_echo_line=echo_arg,
                    track_outcome=track_inject_outcome,
                )
                self._buf = ""
                continue
            o = ord(ch)
            if o in (8, 127):
                if self._buf:
                    self._buf = self._buf[:-1]
                continue
            if o == 9 or o >= 32:
                self._buf += ch
        return True

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height

    async def close(self):
        self._closed = True
        if self._executor:
            try:
                self._executor.close()
            except Exception:
                pass
            self._executor = None

    def is_alive(self) -> bool:
        return not self._closed and self._executor is not None


# ─── 本地 Shell 会话 ─────────────────────────────────────────────────────────

class LocalShellProcess:
    """通过 os.forkpty() 创建真正的 PTY，
    用后台线程 + select.poll() 监听读事件，数据通过 threading.Queue 送入 asyncio。
    避免了非主线程 asyncio.get_event_loop() 在 Python 3.8 下返回未启动 loop 的问题。"""

    def __init__(self, width: int = 80, height: int = 24):
        self.width = width
        self.height = height
        self._master_fd: int = -1
        self._pid: int = -1
        self._output_callback: Optional[callable] = None
        self._closed = False
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None
        self._output_queue: Optional[queue.Queue] = None  # 线程安全队列

    def set_output_callback(self, cb: callable):
        self._output_callback = cb

    def _poll_loop(self):
        """后台线程：持续轮询 PTY，数据放入 Queue（由 _drain_queue 消费）。"""
        import select as _select
        import time as _time
        _queue = self._output_queue
        if not _queue:
            return
        poll_obj = _select.poll()
        poll_obj.register(self._master_fd, _select.POLLIN | _select.POLLHUP)
        while not self._stop_event.is_set():
            events = poll_obj.poll(50)
            for fd, ev in events:
                if ev & _select.POLLIN:
                    try:
                        data = os.read(fd, 4096)
                    except OSError:
                        data = b""
                    if data:
                        try:
                            _queue.put_nowait(data.decode("utf-8", errors="replace"))
                        except queue.Full:
                            pass
                    if not data:
                        self._stop_event.set()
                if ev & (_select.POLLHUP | _select.POLLERR | _select.POLLNVAL):
                    self._stop_event.set()
            if not events:
                time.sleep(0.02)
        try:
            poll_obj.unregister(self._master_fd)
        except Exception:
            pass

    def is_alive(self) -> bool:
        if self._closed:
            return False
        if self._pid <= 0:
            return False
        try:
            os.kill(self._pid, 0)
        except OSError:
            return False
        return True

    async def start(self):
        """fork PTY bash，启动后台 poll 线程。"""
        import fcntl, pty, struct, termios

        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        self._output_queue = queue.Queue()

        pid = os.fork()
        self._pid = pid

        if pid == 0:
            # ── 子进程 ──────────────────────────────────────────────
            os.close(master_fd)

            # 创建新进程组（不调用 setsid，避免 bash 尝试获取控制终端）
            os.setpgrp()

            # 重定向标准输入输出到 slave PTY
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)

            # 设置窗口大小
            winsize = struct.pack("HHHH", self.height, self.width, 0, 0)
            try:
                fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

            os.environ["TERM"] = "xterm-256color"
            # 非交互模式，不会有作业控制警告，stdin 来自 PTY 保持正常工作
            os.execvp("bash", ["bash"])
        else:
            # ── 父进程 ─────────────────────────────────────────────
            os.close(slave_fd)
            self._stop_event = threading.Event()
            self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
            self._poll_thread.start()
            # 启动 asyncio 中的队列消费者
            asyncio.create_task(self._drain_queue())

    async def _drain_queue(self):
        """asyncio 任务：从 Queue 读取 PTY 输出并调用 callback。
        使用 run_in_executor 避免阻塞事件循环。"""
        _queue = self._output_queue
        _cb = self._output_callback
        loop = asyncio.get_running_loop()
        while not self._closed:
            try:
                # run_in_executor 让 Queue.get 不阻塞 asyncio 协程
                data = await loop.run_in_executor(
                    None, lambda: _queue.get(timeout=0.3)
                )
                if data and _cb:
                    await _cb(data)
            except queue.Empty:
                # 队列空，稍等一下再检查
                await asyncio.sleep(0.05)
                continue
            except Exception:
                break

    def _cleanup(self):
        self._closed = True
        if self._stop_event:
            self._stop_event.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=1)
        if self._master_fd >= 0:
            try:
                os.close(self._master_fd)
            except OSError:
                pass
        if self._pid > 0:
            try:
                os.kill(self._pid, 15)
                import time as _time
                _time.sleep(0.2)
                os.kill(self._pid, 9)
            except OSError:
                pass

    async def write(self, data: str):
        """向 master_fd 写入键盘输入。"""
        if self._master_fd < 0 or self._closed:
            return False
        for _ in range(5):  # 重试5次
            try:
                n = os.write(self._master_fd, data.encode("utf-8"))
                return n > 0
            except (BlockingIOError, InterruptedError):
                import time as _t
                _t.sleep(0.005)
            except OSError:
                return False
        return False

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height
        if self._master_fd >= 0:
            import fcntl, struct, termios
            winsize = struct.pack("HHHH", height, width, 0, 0)
            try:
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

    async def close(self):
        """关闭会话。"""
        self._cleanup()


# ─── Windows 本地 Shell（ConPTY：pywinpty，支持退格/方向键等）──────────────────


def _spawn_winpty_process(PtyProcess, argv, *, height: int, width: int):
    """兼容 pywinpty 1.x（encoding 参数）与 2.x（无 encoding）。"""
    kwargs: Dict[str, Any] = {"dimensions": (height, width)}
    try:
        if "encoding" in inspect.signature(PtyProcess.spawn).parameters:
            kwargs["encoding"] = "utf-8"
    except (TypeError, ValueError):
        pass
    return PtyProcess.spawn(argv, **kwargs)


class WindowsPtyShell:
    """Windows 本地：CreatePseudoConsole（经 pywinpty），真实控制台语义，非匿名管道。"""

    def __init__(self, width: int = 80, height: int = 24):
        self.width = width
        self.height = height
        self._proc = None
        self._output_callback: Optional[callable] = None
        self._closed = False
        self._stop_event: Optional[threading.Event] = None
        self._read_thread: Optional[threading.Thread] = None
        self._output_queue: Optional[queue.Queue] = None
        self._drain_task: Optional[asyncio.Task] = None
        self._argv_kind: str = "other"

    def set_output_callback(self, cb: callable):
        self._output_callback = cb

    def _read_loop(self):
        """阻塞读 ConPTY 输出，写入队列供 asyncio 侧广播。"""
        q = self._output_queue
        proc = self._proc
        if not q or not proc:
            return
        while self._stop_event and not self._stop_event.is_set():
            try:
                if not proc.isalive():
                    break
                chunk = proc.read(4096)
                if chunk:
                    q.put(chunk)
                else:
                    time.sleep(0.015)
            except Exception as e:
                logger.debug("WindowsPtyShell 读结束: %s", e)
                break
        try:
            q.put_nowait("")
        except Exception:
            pass

    async def _drain_queue(self):
        _queue = self._output_queue
        _cb = self._output_callback
        loop = asyncio.get_running_loop()
        while not self._closed and _queue is not None:
            try:
                data = await loop.run_in_executor(None, lambda: _queue.get(timeout=0.35))
                if data == "":
                    break
                if data and _cb:
                    await _cb(data)
            except queue.Empty:
                await asyncio.sleep(0.02)
                continue
            except Exception:
                break

    async def start(self) -> bool:
        try:
            from winpty import PtyProcess
        except ImportError:
            logger.warning("未安装 pywinpty，Windows 本地终端将回退到管道模式（退格/编辑可能异常）")
            return False
        argv = _windows_shell_argv()
        self._argv_kind = _windows_argv_kind(argv)
        self._output_queue = queue.Queue()
        self._stop_event = threading.Event()
        try:
            self._proc = _spawn_winpty_process(
                PtyProcess,
                argv,
                height=self.height,
                width=self.width,
            )
        except Exception as e:
            logger.error("WindowsPtyShell 启动失败: %s", e)
            self._proc = None
            return False
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()
        self._drain_task = asyncio.create_task(self._drain_queue())
        logger.info("Windows 本地终端已使用 ConPTY（pywinpty）")
        return True

    async def write(self, data: str) -> bool:
        if self._closed or not self._proc:
            return False
        try:
            self._proc.write(data)
            try:
                self._proc.flush()
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"WindowsPtyShell 写入: {e}")
            return False

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height
        if self._proc:
            try:
                self._proc.setwinsize(height, width)
            except Exception as e:
                logger.debug(f"WindowsPtyShell resize: {e}")

    async def close(self):
        self._closed = True
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
        if self._stop_event:
            self._stop_event.set()
        if self._read_thread and self._read_thread.is_alive():
            self._read_thread.join(timeout=3.0)
        if self._drain_task:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None
        if self._proc:
            try:
                self._proc.close()
            except Exception:
                pass
            self._proc = None

    def is_alive(self) -> bool:
        if self._closed:
            return False
        if not self._proc:
            return False
        try:
            return self._proc.isalive()
        except Exception:
            return False


# ─── Windows 本地 Shell（无 PTY，管道 + asyncio 子进程）──────────────────────

def _windows_shell_argv() -> List[str]:
    """优先 PowerShell，其次 cmd；均非交互式窗口、适合服务端后台。"""
    for exe in ("pwsh.exe", "powershell.exe"):
        path = shutil.which(exe)
        if path:
            return [path, "-NoLogo", "-NoProfile"]
    comspec = os.environ.get("ComSpec") or shutil.which("cmd.exe")
    if not comspec:
        comspec = "cmd.exe"
    # /Q 关闭回显，/K 保持进程不退出以便持续交互
    return [comspec, "/Q", "/K"]


def _windows_argv_kind(argv: List[str]) -> str:
    """本地 shell 可执行类型，用于决定是否可做 PowerShell 退出码包装（cmd 不可套 PS 脚本）。"""
    if not argv:
        return "other"
    base = os.path.basename((argv[0] or "").replace("\\", "/")).lower()
    if base in ("pwsh.exe", "powershell.exe"):
        return "powershell"
    if base == "cmd.exe":
        return "cmd"
    if "pwsh" in base or "powershell" in base:
        return "powershell"
    return "other"


def _decode_shell_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale

        enc = locale.getpreferredencoding(False) or "utf-8"
        return data.decode(enc, errors="replace")


class WindowsPipeShell:
    """Windows 本地终端：无 fork/pty，用异步子进程管道模拟交互。

    与 Unix 上 PTY 相比无真实 TTY（如 vim 全屏可能不完美），但可正常执行命令与查看输出。
    """

    def __init__(self, width: int = 80, height: int = 24):
        self.width = width
        self.height = height
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._output_callback: Optional[callable] = None
        self._closed = False
        self._reader_task: Optional[asyncio.Task] = None
        self._argv_kind: str = "other"

    def set_output_callback(self, cb: callable):
        self._output_callback = cb

    async def _read_stdout(self):
        assert self._proc and self._proc.stdout
        try:
            while not self._closed:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                text = _decode_shell_bytes(chunk)
                if text and self._output_callback:
                    if asyncio.iscoroutinefunction(self._output_callback):
                        await self._output_callback(text)
                    else:
                        await asyncio.get_event_loop().run_in_executor(
                            None, lambda t=text: self._output_callback(t)
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"WindowsPipeShell 读输出结束: {e}")
        finally:
            self._closed = True

    async def start(self) -> bool:
        argv = _windows_shell_argv()
        self._argv_kind = _windows_argv_kind(argv)
        kwargs: dict = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.STDOUT,
        }
        if sys.platform == "win32":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if flags:
                kwargs["creationflags"] = flags
        try:
            self._proc = await asyncio.create_subprocess_exec(*argv, **kwargs)
        except Exception as e:
            logger.error(f"Windows 子进程启动失败: {e}")
            return False
        self._reader_task = asyncio.create_task(self._read_stdout())
        return True

    async def write(self, data: str) -> bool:
        if self._closed or not self._proc or not self._proc.stdin:
            return False
        try:
            self._proc.stdin.write(data.encode("utf-8"))
            await self._proc.stdin.drain()
            return True
        except Exception as e:
            logger.error(f"WindowsPipeShell 写入: {e}")
            return False

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height

    async def close(self):
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None
        if self._proc:
            try:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.debug(f"WindowsPipeShell 关闭进程: {e}")
            self._proc = None

    def is_alive(self) -> bool:
        if self._closed or not self._proc:
            return False
        return self._proc.returncode is None


# ─── 会话管理器 ──────────────────────────────────────────────────────────────

class SessionManager:
    """全局终端会话管理器。"""

    def __init__(self):
        self._sessions: Dict[str, TerminalSession] = {}
        self._shells: Dict[
            str,
            LocalShellProcess
            | SSHShellProcess
            | WinRMShellProcess
            | WinRMPSRPLineShellProcess
            | WindowsPipeShell
            | WindowsPtyShell,
        ] = {}
        self._ws_connections: Dict[str, set] = {}   # session_id → set of websocket clients
        self._lock = asyncio.Lock()
        # AI 计划执行状态（按 session_id）
        self._session_plans: Dict[str, PlanRuntime] = {}
        # 非计划模式 llm_resp：待 confirm 的危险命令（按 session_id，勿用进程级单例）
        self._pending_llm_confirm_cmd: Dict[str, str] = {}
        # 最近一次自然语言原问（供执行结果 LLM 梳理复用）
        self._last_nl_text: Dict[str, str] = {}
        self._ui_locale: Dict[str, str] = {}
        # 终端回显文本尾部（供 LLM 上下文，与键盘 input 缓冲区分）
        self._output_capture: Dict[str, str] = {}
        self._capture_lock = threading.Lock()
        # WinRM：最后一条 WebSocket 断开后延迟释放 Shell，避免远端 wsmprovhost 堆积
        self._winrm_release_tasks: Dict[str, asyncio.Task] = {}

    # ── 会话 CRUD ────────────────────────────────────────────────────────────

    def create_session(
        self,
        host_id: Optional[str] = None,
        title: str = "新终端",
        conn_type: ConnType = ConnType.LOCAL,
        host: str = "127.0.0.1",
        port: int = 22,
        username: str = "",
        password: Optional[str] = None,
        winrm_port: int = 5985,
        winrm_use_ssl: bool = False,
        winrm_transport: str = "ntlm",
        winrm_server_cert_validation: str = "ignore",
        winrm_shell_mode: str = "interactive",
        ssh_private_key_path: Optional[str] = None,
        ssh_private_key_passphrase: Optional[str] = None,
    ) -> TerminalSession:
        sid = str(uuid.uuid4())[:8]
        session = TerminalSession(
            id=sid,
            host_id=host_id,
            title=title,
            conn_type=conn_type,
            status=SessionStatus.PENDING,
            host=host,
            port=port,
            username=username,
            password=password,
            winrm_port=winrm_port,
            winrm_use_ssl=winrm_use_ssl,
            winrm_transport=winrm_transport,
            winrm_server_cert_validation=winrm_server_cert_validation,
            winrm_shell_mode=winrm_shell_mode,
            ssh_private_key_path=ssh_private_key_path,
            ssh_private_key_passphrase=ssh_private_key_passphrase,
        )
        from chibyterm.shell_context import infer_default_target_os

        session.target_os = infer_default_target_os(session)
        self._sessions[sid] = session
        logger.info(f"会话创建: {sid} ({conn_type.value}) — {title}")
        return session

    def refine_session_target_os_from_profile(self, session_id: str, profile: Any) -> Optional[str]:
        """按探测结果更新会话 target_os；返回新值或 None（未变）。"""
        from chibyterm.shell_context import ALLOWED_TARGET_OS, target_os_from_distro_profile

        sess = self._sessions.get(session_id)
        if not sess:
            return None
        tos = target_os_from_distro_profile(profile)
        if not tos or tos not in ALLOWED_TARGET_OS:
            return None
        if (getattr(sess, "target_os", None) or "") == tos:
            return None
        sess.target_os = tos
        return tos

    async def push_session_meta(self, session_id: str) -> None:
        """向会话 WebSocket 推送最新 target_os（自动识别后刷新状态栏）。"""
        from chibyterm.shell_context import session_meta_payload

        sess = self._sessions.get(session_id)
        if not sess:
            return
        await self._broadcast(session_id, session_meta_payload(session_id, sess))

    def apply_host_distro_to_sessions(self, host_id: str, profile: Any) -> list:
        """对绑定该主机的 SSH 会话批量校正 target_os，返回已变更的 session_id 列表。"""
        hid = (host_id or "").strip()
        if not hid or profile is None:
            return []
        changed: list = []
        for sess in list(self._sessions.values()):
            if str(getattr(sess, "host_id", "") or "") != hid:
                continue
            if getattr(sess, "conn_type", None) != ConnType.SSH:
                continue
            if self.refine_session_target_os_from_profile(sess.id, profile):
                changed.append(sess.id)
        return changed

    def get_session(self, session_id: str) -> Optional[TerminalSession]:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list:
        return list(self._sessions.values())

    def update_session(self, session_id: str, **kwargs):
        if session_id in self._sessions:
            for k, v in kwargs.items():
                if hasattr(self._sessions[session_id], k):
                    setattr(self._sessions[session_id], k, v)

    def close_session(self, session_id: str):
        """关闭并清理会话。"""
        asyncio.create_task(self._close_session_async(session_id))

    async def _close_session_async(self, session_id: str):
        self.cancel_winrm_shell_release(session_id)
        host_id = ""
        async with self._lock:
            sess = self._sessions.get(session_id)
            if sess is not None:
                host_id = str(getattr(sess, "host_id", "") or "")
            if session_id in self._shells:
                try:
                    await self._shells[session_id].close()
                except Exception as e:
                    logger.debug(f"关闭 shell {session_id}: {e}")
                del self._shells[session_id]
            if session_id in self._sessions:
                self._sessions[session_id].status = SessionStatus.DISCONNECTED
            if session_id in self._ws_connections:
                del self._ws_connections[session_id]
            self._session_plans.pop(session_id, None)
            self._pending_llm_confirm_cmd.pop(session_id, None)
            self._last_nl_text.pop(session_id, None)
            self._ui_locale.pop(session_id, None)
            self._output_capture.pop(session_id, None)
            logger.info(f"会话已关闭: {session_id}")
        try:
            from chibycore.platform_audit import append_platform_audit

            append_platform_audit(
                "web_terminal_session",
                trace_id=f"sess_{session_id}",
                host_ids=[host_id] if host_id else [],
                result_summary=f"Web 终端会话关闭: {session_id}",
                outcome="success",
                metadata={"session_id": session_id, "action": "close"},
            )
        except Exception:
            logger.debug("web_terminal_session audit skipped", exc_info=True)

    # ── Shell 生命周期 ───────────────────────────────────────────────────────

    @staticmethod
    def _winrm_release_delay_sec() -> float:
        raw = os.environ.get("OPS_WINRM_SHELL_RELEASE_SEC", "8")
        try:
            return max(0.0, min(300.0, float(raw)))
        except (TypeError, ValueError):
            return 8.0

    def has_ws(self, session_id: str) -> bool:
        conns = self._ws_connections.get(session_id)
        return bool(conns)

    def cancel_winrm_shell_release(self, session_id: str) -> None:
        task = self._winrm_release_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    def schedule_winrm_shell_release(self, session_id: str) -> None:
        """无 WebSocket 时延迟关闭 WinRM Shell（保留会话元数据，便于重连 start_shell）。"""
        session = self._sessions.get(session_id)
        if not session or session.conn_type != ConnType.WINRM:
            return
        if self.has_ws(session_id):
            return
        if session_id not in self._shells:
            return
        self.cancel_winrm_shell_release(session_id)
        delay = self._winrm_release_delay_sec()

        async def _job() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                if self.has_ws(session_id):
                    return
                sess = self._sessions.get(session_id)
                if not sess or sess.conn_type != ConnType.WINRM:
                    return
                if session_id not in self._shells:
                    return
                await self.detach_dead_shell(session_id)
                logger.info("WinRM Shell 已因无 WebSocket 连接释放: %s", session_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("WinRM shell release failed %s", session_id, exc_info=True)
            finally:
                cur = self._winrm_release_tasks.get(session_id)
                if cur is asyncio.current_task():
                    self._winrm_release_tasks.pop(session_id, None)

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._winrm_release_tasks[session_id] = loop.create_task(_job())

    async def start_shell(self, session_id: str, width: int = 120, height: int = 30) -> bool:
        """启动 shell（本地 PTY 或 SSH）并关联到会话。"""
        session = self._sessions.get(session_id)
        if not session:
            return False

        self.cancel_winrm_shell_release(session_id)
        # 避免重复 start 泄漏旧 Shell（尤其 WinRM wsmprovhost）
        old_shell = None
        async with self._lock:
            old_shell = self._shells.pop(session_id, None)
        if old_shell is not None:
            try:
                await old_shell.close()
            except Exception:
                logger.debug("替换启动前关闭旧 shell 失败 %s", session_id, exc_info=True)

        session.status = SessionStatus.CONNECTING
        session.last_active = datetime.utcnow()
        session.last_error = None

        try:
            if session.conn_type == ConnType.SSH:
                # SSH 会话
                shell = SSHShellProcess(
                    host=session.host,
                    port=session.port,
                    username=session.username,
                    password=session.password,
                    width=width,
                    height=height,
                    pkey_path=session.ssh_private_key_path,
                    pkey_passphrase=session.ssh_private_key_passphrase,
                )
            elif session.conn_type == ConnType.WINRM:
                if getattr(session, "winrm_shell_mode", "interactive") == "psrp_line":
                    shell = WinRMPSRPLineShellProcess(
                        host=session.host,
                        winrm_port=session.winrm_port,
                        username=session.username,
                        password=session.password,
                        use_ssl=session.winrm_use_ssl,
                        transport=session.winrm_transport or "ntlm",
                        server_cert_validation=session.winrm_server_cert_validation or "ignore",
                        width=width,
                        height=height,
                    )
                else:
                    shell = WinRMShellProcess(
                        host=session.host,
                        winrm_port=session.winrm_port,
                        username=session.username,
                        password=session.password,
                        use_ssl=session.winrm_use_ssl,
                        transport=session.winrm_transport or "ntlm",
                        server_cert_validation=session.winrm_server_cert_validation or "ignore",
                        width=width,
                        height=height,
                    )
            elif sys.platform == "win32":
                # 优先 ConPTY（退格/方向键需真实控制台）；失败则回退管道子进程
                pty_shell = WindowsPtyShell(width=width, height=height)
                if await pty_shell.start():
                    shell = pty_shell
                else:
                    shell = WindowsPipeShell(width=width, height=height)
            else:
                # Unix / Linux：真 PTY + bash
                shell = LocalShellProcess(width=width, height=height)

            async def safe_broadcast(data: str):
                """确保异步广播不泄漏未 await 的协程。"""
                try:
                    payload = {
                        "type": "output",
                        "session_id": session_id,
                        "data": data,
                    }
                    if isinstance(shell, WinRMPSRPLineShellProcess):
                        payload["psrp_cwd"] = shell._cwd
                    await self._broadcast(session_id, payload)
                except Exception as e:
                    logger.debug(f"Broadcast error: {e}")

            async def output_callback(data: str):
                await safe_broadcast(data)

            shell.set_output_callback(output_callback)

            if session.conn_type == ConnType.SSH:
                success = await shell.start()
            elif session.conn_type == ConnType.WINRM:
                success = await shell.start()
            elif isinstance(shell, (WindowsPipeShell, WindowsPtyShell)):
                if isinstance(shell, WindowsPtyShell):
                    success = True
                else:
                    success = await shell.start()
            else:
                await shell.start()
                success = True

            if not success:
                session.status = SessionStatus.ERROR
                detail = None
                if session.conn_type == ConnType.WINRM and isinstance(
                    shell, (WinRMShellProcess, WinRMPSRPLineShellProcess)
                ):
                    detail = shell._connect_error
                elif session.conn_type == ConnType.SSH and isinstance(shell, SSHShellProcess):
                    detail = shell._connect_error
                base = (detail or "").strip()
                if session.conn_type == ConnType.WINRM and base:
                    base = base + _winrm_error_hints(base)
                session.last_error = base or "Shell 启动失败，请检查系统环境"
                logger.error(f"Shell 启动失败 {session_id}: {session.last_error}")
                return False

            async with self._lock:
                self._shells[session_id] = shell

            session.status = SessionStatus.CONNECTED
            logger.info(f"Shell 已启动: {session_id} ({session.conn_type.value})")
            return True
        except Exception as e:
            session.status = SessionStatus.ERROR
            msg = str(e)
            if session.conn_type == ConnType.WINRM:
                msg = msg + _winrm_error_hints(msg)
            session.last_error = msg
            logger.error(f"Shell 启动失败 {session_id}: {e}")
            return False

    async def shell_input(self, session_id: str, data: str, *, echo_psrp_line: bool = False):
        """向 shell 发送输入。

        echo_psrp_line：为 True 时
        - WinRM PSRP 按行：执行行前向终端广播 ``PS <cwd>> <命令>``（见 WinRMPSRPLineShellProcess）。
        - 本地 Windows ConPTY/管道：写入前将裸 LF 规范为 CRLF，避免续行 ``>>``。
          若会话为 PowerShell 且注入为**单行**（无内嵌换行），默认用与 PSRP 相同的 Base64 包装，
          在输出末行写入 ``__OPS_EXIT_CODE__:<n>`` 供服务端解析（``OPS_LOCAL_PS_NO_EXIT_WRAP=1`` 可关闭）。
          默认**不再**向前端推送 ``PS> <命令>`` 镜像：ConPTY 侧通常会回显注入行，再推镜像会重复
          （先出现 ``PS> pwd`` 再出现一行 ``pwd``）。若你的环境注入后仍看不到命令，可设
          ``OPS_WIN_CONPTY_INJECT_MIRROR=1`` 显式开启镜像行。
        """
        async with self._lock:
            shell = self._shells.get(session_id)
        if shell:
            if isinstance(shell, WinRMPSRPLineShellProcess):
                await shell.write(
                    data,
                    echo_psrp_line=echo_psrp_line,
                    track_inject_outcome=echo_psrp_line,
                )
            else:
                if isinstance(shell, (WindowsPtyShell, WindowsPipeShell)):
                    raw_in = data
                    if echo_psrp_line:
                        # 去掉尾随换行再统一加一个 \n，经 normalize 后仅一对 CRLF，避免「空行」二次提交在 PSReadLine 下出现 >> 
                        raw_in = raw_in.rstrip("\r\n") + "\n"
                    mirror_cmd: Optional[str] = None
                    if (
                        echo_psrp_line
                        and not _env_truthy("OPS_LOCAL_PS_NO_EXIT_WRAP")
                        and getattr(shell, "_argv_kind", "") == "powershell"
                    ):
                        d = raw_in.rstrip("\r\n")
                        if d.strip() and "\n" not in d and "\r" not in d:
                            try:
                                raw_in = wrap_powershell_script_for_exit_marker(d)
                                mirror_cmd = d
                            except Exception:
                                logger.debug("本地 PowerShell 退出码包装失败，回退原行", exc_info=True)
                    data = _normalize_win_conpty_input(raw_in)
                    if echo_psrp_line and _env_truthy("OPS_WIN_CONPTY_INJECT_MIRROR"):
                        vis_source = mirror_cmd if mirror_cmd is not None else data
                        vis = vis_source.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
                        for ln in vis.split("\n"):
                            s = ln.strip()
                            if s:
                                mirror = "\r\n\x1b[33mPS>\x1b[0m " + s + "\r\n"
                                await self._broadcast(
                                    session_id,
                                    {
                                        "type": "output",
                                        "session_id": session_id,
                                        "data": mirror,
                                    },
                                )
                await shell.write(data)
            self._sessions[session_id].last_active = datetime.utcnow()
            if "\n" in data or "\r" in data:
                try:
                    from chibycore.transcript import append_transcript

                    append_transcript(session_id, "in", data)
                except Exception:
                    pass

    def reset_psrp_inject_batch(self, session_id: str) -> None:
        """新一轮「服务端注入」批处理前清零 PSRP 退出码累计（与 cap_mark 对齐）。"""
        shell = self._shells.get(session_id)
        if isinstance(shell, WinRMPSRPLineShellProcess):
            shell.reset_psrp_inject_batch()

    def consume_psrp_inject_batch_outcome(self, session_id: str) -> Optional[Tuple[str, Optional[int]]]:
        """读取并清空最近一次注入批次的 PSRP 成败与退出码；非 PSRP 会话返回 None。"""
        shell = self._shells.get(session_id)
        if isinstance(shell, WinRMPSRPLineShellProcess):
            return shell.consume_psrp_inject_batch_outcome()
        return None

    async def shell_resize(self, session_id: str, width: int, height: int):
        """调整 shell 尺寸。"""
        async with self._lock:
            shell = self._shells.get(session_id)
        if shell:
            await shell.resize(width, height)

    def has_active_shell(self, session_id: str) -> bool:
        return session_id in self._shells

    def shell_is_alive(self, session_id: str) -> bool:
        shell = self._shells.get(session_id)
        if not shell:
            return False
        fn = getattr(shell, "is_alive", None)
        if callable(fn):
            try:
                return bool(fn())
            except Exception:
                return False
        return True

    async def broadcast_session_error(
        self,
        session_id: str,
        reason: str,
        detail: str = "",
    ) -> None:
        """向该会话全部 WebSocket 客户端推送连接异常（不写 output_capture）。"""
        await self._broadcast(
            session_id,
            {
                "type": "session_error",
                "session_id": session_id,
                "reason": reason,
                "detail": detail or "",
            },
        )

    async def detach_dead_shell(self, session_id: str) -> None:
        """判定 Shell 已死后移除后端实例并标记会话断开（保留会话记录便于前端提示）。"""
        shell = None
        async with self._lock:
            shell = self._shells.pop(session_id, None)
        if shell:
            try:
                await shell.close()
            except Exception:
                logger.debug("detach_dead_shell close failed", exc_info=True)
        sess = self._sessions.get(session_id)
        if sess:
            sess.status = SessionStatus.DISCONNECTED

    # ── WebSocket 连接注册 ─────────────────────────────────────────────────

    def register_ws(self, session_id: str, ws):
        self.cancel_winrm_shell_release(session_id)
        if session_id not in self._ws_connections:
            self._ws_connections[session_id] = set()
        self._ws_connections[session_id].add(ws)

    def unregister_ws(self, session_id: str, ws):
        if session_id in self._ws_connections:
            self._ws_connections[session_id].discard(ws)
            if not self._ws_connections[session_id]:
                del self._ws_connections[session_id]

    # ── AI 计划状态 ─────────────────────────────────────────────────────────

    def get_terminal_plan(self, session_id: str) -> Optional[PlanRuntime]:
        return self._session_plans.get(session_id)

    def set_terminal_plan(self, session_id: str, plan: Optional[PlanRuntime]) -> None:
        if plan is None:
            self._session_plans.pop(session_id, None)
        else:
            self._session_plans[session_id] = plan

    def clear_terminal_plan(self, session_id: str) -> None:
        self._session_plans.pop(session_id, None)

    def set_pending_llm_confirm_command(self, session_id: str, cmd: Optional[str]) -> None:
        """单条 llm_resp 需确认执行时暂存命令；与 WebSocket 会话一一对应。"""
        if cmd and str(cmd).strip():
            self._pending_llm_confirm_cmd[session_id] = str(cmd)
        else:
            self._pending_llm_confirm_cmd.pop(session_id, None)

    def get_pending_llm_confirm_command(self, session_id: str) -> Optional[str]:
        return self._pending_llm_confirm_cmd.get(session_id)

    def set_last_nl_text(self, session_id: str, text: Optional[str]) -> None:
        """记住最近自然语言原问，供执行结果 Markdown 梳理使用。"""
        t = (text or "").strip()
        if t:
            self._last_nl_text[session_id] = t[:2000]
        else:
            self._last_nl_text.pop(session_id, None)

    def get_last_nl_text(self, session_id: str) -> str:
        return self._last_nl_text.get(session_id) or ""

    def set_ui_locale(self, session_id: str, locale: Optional[str]) -> None:
        """记住会话 UI/AI 语言（简体/繁体/英文），供 LLM 说明与结果梳理使用。"""
        from chibyterm.ui_locale import normalize_ui_locale

        if not session_id:
            return
        self._ui_locale[session_id] = normalize_ui_locale(locale)

    def get_ui_locale(self, session_id: str) -> str:
        from chibyterm.ui_locale import DEFAULT_LOCALE

        return self._ui_locale.get(session_id) or DEFAULT_LOCALE

    def append_output_capture(self, session_id: str, data: str) -> None:
        """累积终端输出尾部，供 LLM 与后续验算使用。"""
        if not data:
            return
        with self._capture_lock:
            prev = self._output_capture.get(session_id, "")
            merged = (prev + data)[-TERMINAL_CAPTURE_RING_MAX_CHARS:]
            self._output_capture[session_id] = merged

    def schedule_terminal_output(
        self,
        loop: asyncio.AbstractEventLoop,
        session_id: str,
        data: str,
        *,
        closure_mirror: bool = False,
    ) -> None:
        """从 executor / 子线程注入左侧终端：走 WebSocket「output」并与 capture 同源。"""
        if not session_id or not data:
            return

        payload: Dict[str, Any] = {
            "type": "output",
            "session_id": session_id,
            "data": data,
        }
        if closure_mirror:
            payload["closure_mirror"] = True

        async def _emit() -> None:
            await self._broadcast(session_id, payload)

        try:
            asyncio.run_coroutine_threadsafe(_emit(), loop)
        except RuntimeError:
            with self._capture_lock:
                prev = self._output_capture.get(session_id, "")
                self._output_capture[session_id] = (prev + data)[-TERMINAL_CAPTURE_RING_MAX_CHARS:]

    def get_output_capture(self, session_id: str) -> str:
        return self._output_capture.get(session_id, "")

    async def _broadcast(self, session_id: str, message: dict):
        """向所有连接到该 session 的 WebSocket 广播消息。"""
        if message.get("type") == "output" and isinstance(message.get("data"), str):
            self.append_output_capture(session_id, message["data"])
            try:
                from chibycore.transcript import append_transcript

                append_transcript(session_id, "out", message["data"])
            except Exception:
                pass
        if session_id not in self._ws_connections:
            return
        dead = set()
        for ws in self._ws_connections[session_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._ws_connections[session_id].discard(ws)


# ─── 单例 ───────────────────────────────────────────────────────────────────

_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager
