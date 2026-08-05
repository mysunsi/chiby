"""WebSocket SSH 终端 — Phase 1 核心。

技术路线：
  - SSH: paramiko SSHClient → invoke_shell() 持久 PTY channel
  - 本地: os.forkpty() 真正 PTY + asyncio 事件循环桥接
  - 协议: JSON {"type": "input"|"resize"|"ping", "data": "...", "width": N, "height": N}
"""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
import struct
import termios
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── 会话上下文 ───────────────────────────────────────────────────────────────

@dataclass
class SessionContext:
    session_id: str
    host: str
    port: int
    username: str
    conn_type: str          # "ssh" | "local"
    password: Optional[str] = None
    private_key: Optional[str] = None
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_active: datetime = field(default_factory=datetime.utcnow)
    error_msg: str = ""


# ─── 本地 PTY（os.forkpty）────────────────────────────────────────────────────

class LocalPTYSession:
    """通过 os.forkpty() 创建真正的 PTY，master fd 直连 asyncio 事件循环。"""

    def __init__(self, width: int = 80, height: int = 24):
        self.width = width
        self.height = height
        self._master_fd: int = -1
        self._pid: int = -1
        self._reader_task: Optional[asyncio.Task] = None
        self._ws: Optional[WebSocket] = None
        self._closed = False

    async def connect(self, ws: WebSocket):
        """fork PTY，在父进程中启动异步读取协程。"""
        self._ws = ws
        self._closed = False

        master_fd, slave_fd = os.openpty()
        self._master_fd = master_fd

        # master 设为非阻塞
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        pid = os.fork()
        self._pid = pid

        if pid == 0:
            # ── 子进程 ──────────────────────────────────────────────────
            os.close(master_fd)
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            os.close(slave_fd)
            # 设置初始窗口大小
            winsize = struct.pack("HHHH", self.height, self.width, 0, 0)
            fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)
            # 启动交互式 bash
            os.execvp("bash", ["bash", "--norc", "--noediting", "-i"])
        else:
            # ── 父进程 ────────────────────────────────────────────────
            os.close(slave_fd)
            self._reader_task = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        """从 master_fd 持续读取 → 推给 WebSocket。"""
        buf = bytearray(4096)
        try:
            while not self._closed:
                await asyncio.sleep(0.02)
                try:
                    n = os.read(self._master_fd, buf, 4096)
                    if not n:
                        break
                    if self._ws:
                        try:
                            await self._ws.send_text(
                                buf[:n].decode("utf-8", errors="replace")
                            )
                        except Exception:
                            break
                except (BlockingIOError, InterruptedError):
                    continue
                except Exception as e:
                    if not self._closed:
                        logger.debug(f"PTY read: {e}")
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"PTY read loop done: {e}")
        finally:
            self._cleanup()

    def _cleanup(self):
        self._closed = True
        try:
            os.close(self._master_fd)
        except Exception:
            pass
        if self._pid > 0:
            try:
                os.kill(self._pid, signal.SIGTERM)
                time.sleep(0.2)
                os.kill(self._pid, signal.SIGKILL)
            except Exception:
                pass

    async def write(self, data: str):
        """向 master_fd 写入键盘输入。"""
        if self._master_fd >= 0 and not self._closed:
            try:
                os.write(self._master_fd, data.encode("utf-8"))
            except (BlockingIOError, InterruptedError):
                pass
            except Exception as e:
                logger.debug(f"PTY write: {e}")

    async def resize(self, width: int, height: int):
        self.width = width
        self.height = height
        if self._master_fd >= 0:
            winsize = struct.pack("HHHH", height, width, 0, 0)
            try:
                fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    async def close(self):
        self._closed = True
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        self._cleanup()


# ─── SSH PTY 会话（paramiko）──────────────────────────────────────────────────

class SSHSession:
    """通过 paramiko 建立持久 SSH 会话，支持 PTY shell。"""

    def __init__(self, host: str, port: int, username: str,
                 password: Optional[str] = None,
                 private_key: Optional[str] = None,
                 width: int = 80, height: int = 24):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key = private_key
        self.width = width
        self.height = height
        self._client = None
        self._channel = None
        self._reader_task: Optional[asyncio.Task] = None
        self._ws: Optional[WebSocket] = None
        self._closed = False
        self._q: "queue.Queue" = None
        self._thread: Optional[threading.Thread] = None

    async def connect(self, ws: WebSocket):
        import queue
        self._ws = ws
        self._closed = False
        self._q = queue.Queue()

        def _ssh_connect():
            try:
                import paramiko
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                kw = {
                    "hostname": self.host, "port": self.port,
                    "username": self.username, "timeout": 15,
                }
                if self.private_key:
                    kw["key_filename"] = self.private_key
                else:
                    kw["password"] = self.password
                client.connect(**kw)
                try:
                    transport = client.get_transport()
                    if transport is not None:
                        transport.set_keepalive(30)
                except Exception:
                    pass
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
                        if channel.exit_status_ready():
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
                self._q.put(f"\r\n[SSH 连接错误: {e}]\r\n")
                self._q.put("")
                self._closed = True

        self._thread = threading.Thread(target=_ssh_connect, daemon=True)
        self._thread.start()

        # 等连接建立（最多 15s）
        start = time.time()
        while self._channel is None and time.time() - start < 15:
            await asyncio.sleep(0.2)
            if self._closed:
                return

        if self._closed or self._channel is None:
            await ws.send_text("[连接超时]\r\n")
            return

        self._reader_task = asyncio.create_task(self._read_queue())

    async def _read_queue(self):
        import queue
        try:
            while not self._closed:
                await asyncio.sleep(0.05)
                try:
                    while True:
                        data = self._q.get_nowait()
                        if not data:
                            self._closed = True
                            break
                        if self._ws:
                            try:
                                await self._ws.send_text(data)
                            except Exception:
                                break
                except queue.Empty:
                    if self._closed:
                        break
                    continue
        except Exception as e:
            logger.debug(f"SSH queue read done: {e}")
        finally:
            self._closed = True

    async def write(self, data: str):
        if self._channel and not self._closed:
            try:
                self._channel.send(data)
            except Exception as e:
                logger.error(f"SSH write: {e}")

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


# ─── 全局会话管理器 ─────────────────────────────────────────────────────────

class TerminalSessionManager:
    def __init__(self):
        self.sessions: Dict[str, SessionContext] = {}
        self.backends: Dict[str, LocalPTYSession | SSHSession] = {}
        self._lock = asyncio.Lock()

    async def create(self, session_id: str, host: str, port: int,
                     username: str, conn_type: str,
                     password: Optional[str] = None,
                     private_key: Optional[str] = None) -> SessionContext:
        ctx = SessionContext(
            session_id=session_id, host=host, port=port,
            username=username, conn_type=conn_type,
            password=password, private_key=private_key,
            status="connecting",
        )
        async with self._lock:
            self.sessions[session_id] = ctx
        return ctx

    async def set_connected(self, session_id: str, backend):
        async with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].status = "connected"
                self.sessions[session_id].last_active = datetime.utcnow()
            self.backends[session_id] = backend

    async def set_error(self, session_id: str, msg: str):
        async with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].status = "error"
                self.sessions[session_id].error_msg = msg

    async def close(self, session_id: str):
        async with self._lock:
            if session_id in self.backends:
                await self.backends[session_id].close()
                del self.backends[session_id]
            if session_id in self.sessions:
                self.sessions[session_id].status = "disconnected"

    def list(self) -> list[SessionContext]:
        return list(self.sessions.values())

    def get(self, session_id: str) -> Optional[SessionContext]:
        return self.sessions.get(session_id)


session_mgr = TerminalSessionManager()


# ─── WebSocket 路由 ────────────────────────────────────────────────────────────

@router.websocket("/ws/terminal/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str):
    """WebSocket 终端主入口。

    连接建立后，前端发送认证帧：
      {"type": "auth", "conn_type": "ssh"|"local",
       "host": "...", "port": 22, "username": "...",
       "password": "...", "width": 120, "height": 30}
    """
    await websocket.accept()
    logger.info(f"WS connected: {session_id}")

    try:
        raw = await websocket.receive_text()
        auth = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        await websocket.send_text("[错误] 认证帧无效\r\n")
        await websocket.close()
        return

    conn_type = auth.get("conn_type", "local")
    width     = auth.get("width", 120)
    height    = auth.get("height", 30)
    host      = auth.get("host", "127.0.0.1")
    port      = auth.get("port", 22)
    username  = auth.get("username", "")
    password  = auth.get("password")
    pkey      = auth.get("private_key")

    ctx = await session_mgr.create(
        session_id, host, port, username, conn_type,
        password=password, private_key=pkey,
    )

    if conn_type == "local":
        backend: LocalPTYSession | SSHSession = LocalPTYSession(width, height)
    else:
        backend = SSHSession(host, port, username, password, pkey, width, height)

    try:
        await backend.connect(websocket)
        await session_mgr.set_connected(session_id, backend)
        await websocket.send_text(f"[已连接 {conn_type}: {username}@{host}:{port}]\r\n")
    except Exception as e:
        await session_mgr.set_error(session_id, str(e))
        await websocket.send_text(f"[连接失败: {e}]\r\n")
        await websocket.close()
        return

    # 主循环
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            t = msg.get("type", "input")

            if t == "input":
                await backend.write(msg.get("data", ""))
                ctx.last_active = datetime.utcnow()

            elif t == "resize":
                await backend.resize(msg.get("width", 120), msg.get("height", 30))

            elif t == "ping":
                await websocket.send_json({"type": "pong", "session_id": session_id})

    except WebSocketDisconnect:
        logger.info(f"WS disconnected: {session_id}")
    except Exception as e:
        logger.error(f"WS session error: {e}")
    finally:
        await session_mgr.close(session_id)


# ─── HTTP 辅助端点 ────────────────────────────────────────────────────────────

@router.get("/api/v1/terminal/sessions")
async def list_sessions():
    return {
        "count": len(session_mgr.sessions),
        "sessions": [
            {
                "session_id": s.session_id,
                "host": s.host, "port": s.port,
                "username": s.username,
                "conn_type": s.conn_type,
                "status": s.status,
                "created_at": s.created_at.isoformat(),
                "last_active": s.last_active.isoformat(),
                "error_msg": s.error_msg,
            }
            for s in session_mgr.list()
        ],
    }


@router.delete("/api/v1/terminal/sessions/{session_id}")
async def close_session(session_id: str):
    await session_mgr.close(session_id)
    return {"ok": True, "session_id": session_id}
