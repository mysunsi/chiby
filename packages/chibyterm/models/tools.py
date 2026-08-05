"""远端工具协议契约（白名单常量 + ToolCall 数据类）。

实现（解析 / 执行）仍在 ``terminal.mobile.remote_tools``；本模块仅契约面。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypedDict


class ToolSchema(TypedDict, total=False):
    """工具描述最小契约（文档 / 注册表用）。"""

    name: str
    description: str
    readonly: bool
    needs_host: bool


DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "host_list",
    "kb_search",
    "kb_get",
    "kb_ingest",
    "doc_search",
    "doc_get",
    "search_knowledge",
    "get_content",
    "example_echo",
    "ssh_execute",
    "winrm_execute",
    "ssh_batch",
    "winrm_batch",
    "process_list",
    "service_status",
    "log_search",
    "network_connections",
    "remote_run",
    "remote_list_dir",
    "remote_read_file",
    "remote_write_file",
    "remote_mkdir",
    "remote_remove",
    "remote_grep",
    "remote_search",
    "remote_diff",
    "remote_backup",
    "remote_restore",
    "remote_rollback",
    "remote_syntax_check",
    "remote_logs",
)

FILE_TOOLS: frozenset[str] = frozenset(
    {
        "remote_list_dir",
        "remote_read_file",
        "remote_write_file",
        "remote_mkdir",
        "remote_remove",
        "remote_grep",
        "remote_search",
        "remote_diff",
        "remote_backup",
        "remote_restore",
        "remote_syntax_check",
        "remote_logs",
    }
)
FILE_READONLY_TOOLS: frozenset[str] = frozenset(
    {
        "remote_list_dir",
        "remote_read_file",
        "remote_grep",
        "remote_search",
        "remote_diff",
        "remote_backup",
        "remote_syntax_check",
        "remote_logs",
    }
)
FILE_ALWAYS_CONFIRM: frozenset[str] = frozenset(
    {"remote_write_file", "remote_remove", "remote_restore", "remote_rollback"}
)

LOCAL_NO_HOST_BUILTIN: frozenset[str] = frozenset(
    {
        "host_list",
        "kb_search",
        "kb_get",
        "kb_ingest",
        "doc_search",
        "doc_get",
        "search_knowledge",
        "get_content",
        "example_echo",
    }
)

DEFAULT_READ_BYTES = 8_000

# 兼容旧私有名
_DEFAULT_READ_BYTES = DEFAULT_READ_BYTES


@dataclass
class RemoteToolCall:
    """远端 / 本地工具调用请求（协议信封）。"""

    tool: str
    host: str = ""
    hosts: List[str] = field(default_factory=list)
    command: str = ""
    script: str = ""
    timeout_sec: float = 60.0
    path: str = ""
    content: str = ""
    recursive: bool = False
    max_bytes: Optional[int] = None
    offset: int = 0
    tail_lines: int = 0
    pattern: str = ""
    context: int = 0
    glob: str = ""
    max_hits: int = 40
    filter: str = ""
    lines: int = 50
    lang: str = ""
    backup_path: str = ""
    stream: bool = False
    attachment_id: str = ""
    content_bytes: Optional[bytes] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def shell_text(self) -> str:
        # 命令面工具：确认/高危判定必须用 command/script，不能被 host 预览抢走
        if self.tool in (
            "remote_run",
            "ssh_execute",
            "winrm_execute",
            "ssh_batch",
            "winrm_batch",
        ):
            direct = (self.command or self.script or "").strip()
            if not direct and isinstance(self.raw, dict):
                direct = str(
                    self.raw.get("command") or self.raw.get("script") or ""
                ).strip()
            if direct:
                return direct
        if self.tool == "kb_ingest":
            title = str((self.raw or {}).get("title") or "").strip()
            return f"kb_ingest · {title}" if title else "kb_ingest"
        if self.tool == "kb_search":
            q = str(
                (self.raw or {}).get("q")
                or (self.raw or {}).get("query")
                or ""
            ).strip()
            return f"kb_search · {q}" if q else "kb_search"
        if self.tool == "kb_get":
            eid = str(
                (self.raw or {}).get("entry_id")
                or (self.raw or {}).get("id")
                or ""
            ).strip()
            return f"kb_get · {eid}" if eid else "kb_get"
        if self.tool == "doc_search":
            q = str(
                (self.raw or {}).get("q")
                or (self.raw or {}).get("query")
                or ""
            ).strip()
            return f"doc_search · {q}" if q else "doc_search"
        if self.tool == "doc_get":
            cid = str((self.raw or {}).get("chunk_id") or "").strip()
            did = str((self.raw or {}).get("doc_id") or "").strip()
            key = cid or did
            return f"doc_get · {key}" if key else "doc_get"
        if self.tool == "search_knowledge":
            q = str(
                (self.raw or {}).get("q")
                or (self.raw or {}).get("query")
                or ""
            ).strip()
            return f"search_knowledge · {q}" if q else "search_knowledge"
        if self.tool == "get_content":
            fid = str(
                (self.raw or {}).get("full_id")
                or (self.raw or {}).get("id")
                or ""
            ).strip()
            return f"get_content · {fid[:60]}" if fid else "get_content"
        try:
            from chibyterm.tools_plugin_loader import is_plugin_tool, plugin_shell_text

            if is_plugin_tool(self.tool):
                return plugin_shell_text(self.tool, self.raw or {})
        except Exception:
            pass
        if self.tool == "remote_run":
            return (self.command or self.script or "").strip()
        if (self.command or self.script or "").strip() and self.tool not in FILE_TOOLS:
            return (self.command or self.script or "").strip()
        if self.tool in FILE_TOOLS and (
            self.path or self.tool in ("remote_grep", "remote_search")
        ):
            if self.tool == "remote_write_file":
                if self.content_bytes is not None:
                    n = len(self.content_bytes)
                else:
                    n = len(self.content.encode("utf-8")) if self.content else 0
                return f"remote_write_file {self.path} ({n} bytes)"
            if self.tool == "remote_remove":
                flag = " -r" if self.recursive else ""
                return f"remote_remove{flag} {self.path}"
            if self.tool == "remote_read_file":
                bits = [self.tool, self.path]
                if int(self.tail_lines or 0) > 0:
                    bits.append(f"tail={int(self.tail_lines)}")
                else:
                    mb = self.max_bytes
                    if mb is None:
                        bits.append(f"max={DEFAULT_READ_BYTES}")
                    else:
                        bits.append(f"max={int(mb)}")
                    if int(self.offset or 0) > 0:
                        bits.append(f"off={int(self.offset)}")
                return " ".join(bits)
            if self.tool in ("remote_grep", "remote_search"):
                return (
                    f"remote_grep {self.path} pattern={self.pattern!r} "
                    f"ctx={int(self.context or 0)} max={int(self.max_hits or 40)}"
                )
            if self.tool == "remote_logs":
                return (
                    f"remote_logs {self.path} lines={int(self.lines or 50)}"
                    + (f" filter={self.filter!r}" if self.filter else "")
                )
            if self.tool == "remote_syntax_check":
                return f"remote_syntax_check {self.path} lang={self.lang or 'python'}"
            if self.tool == "remote_restore":
                return (
                    f"remote_restore path={self.path} backup={self.backup_path}".strip()
                )
            return f"{self.tool} {self.path}".strip()
        return ""


# 别名：文档 / 验收中的 ToolCall
ToolCall = RemoteToolCall
