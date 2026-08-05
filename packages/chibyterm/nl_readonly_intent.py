"""自然语言只读窄问：严格意图分类（高效型 / 多机模板 / llm_shell 同源）。

原则：准确优先——「进程占内存」不得绑成 ``free -h``；不够具体则返回 None。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ReadonlyIntentHit:
    """命中的只读意图。"""

    label: str
    command: str
    kind: str = ""  # mem | disk | load | proc_mem | hostname


def normalize_nl_query(text: str) -> str:
    """轻量规范化：取首行、全角标点、压缩空白。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    first = next((ln.strip() for ln in raw.splitlines() if ln.strip()), raw)
    q = first if len(first) <= 200 else first[:200]
    trans = str.maketrans(
        {
            "？": "?",
            "！": "!",
            "，": ",",
            "。": ".",
            "：": ":",
            "；": ";",
            "（": "(",
            "）": ")",
            "【": "[",
            "】": "]",
            "\u3000": " ",
        }
    )
    q = q.translate(trans)
    q = re.sub(r"\s+", " ", q).strip()
    return q


_PROC_MEM_RE = re.compile(
    r"(?i)"
    r"(?:哪些|哪[个些]|谁|什么).{0,12}(?:进程|process).{0,12}(?:内存|mem|memory)|"
    r"(?:进程|process).{0,16}(?:占用|占|吃|最大|最高|排行|top).{0,8}(?:内存|mem|memory)|"
    r"(?:内存|mem|memory).{0,16}(?:占用|占).{0,8}(?:最大|最高|最多).{0,8}(?:进程|process)|"
    r"(?:占用内存|占内存|吃内存|WorkingSet)|"
    r"(?:内存占用最大的进程|最大内存进程|top\s*mem)",
)

_HOSTNAME_RE = re.compile(
    r"(?i)主机名|hostname|计算机名|电脑名|当前主机名|(?:当前主机)(?!名?上)"
)

_DISK_RE = re.compile(
    r"(?i)"
    r"(?:磁盘|硬盘|disk|\bdf\b).{0,12}(?:还剩|剩余|空闲|多少|空间|使用|用量)|"
    r"(?:还剩|剩余|空闲|多少).{0,8}(?:磁盘|硬盘|disk|空间)|"
    r"(?:磁盘|硬盘)\s*(?:空间|用量|使用情况)?|"
    r"(?:查|看|查看).{0,6}(?:磁盘|硬盘|disk)",
)

_LOAD_RE = re.compile(
    r"(?i)"
    r"(?:系统\s*)?负载|(?:\buptime\b)|(?:cpu\s*负载)|(?:系统负载多少)|"
    r"(?:负载多少|负载怎么样)",
)

_MEM_FREE_RE = re.compile(
    r"(?i)"
    r"(?:内存|memory|\bmem\b).{0,12}(?:还剩|剩余|空闲|可用|多少|总量|总共|free|available)|"
    r"(?:还剩|剩余|空闲|可用).{0,8}(?:内存|memory)|"
    r"(?:查|看|查看).{0,6}(?:内存|memory)|"
    r"(?:free\s*-h)",
)


def _cmds_for(conn_type: str) -> dict[str, tuple[str, str]]:
    ct = (conn_type or "ssh").strip().lower() or "ssh"
    if ct == "winrm":
        return {
            "proc_mem": (
                "查看内存占用最高的进程",
                "Get-Process | Sort-Object WorkingSet64 -Descending | "
                "Select-Object -First 10 Name,Id,"
                "@{N='MemoryMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} | "
                "Format-List | Out-String",
            ),
            "hostname": ("查看主机名", "$env:COMPUTERNAME"),
            "disk": (
                "查看磁盘空间",
                "Get-CimInstance Win32_LogicalDisk -Filter \"DriveType=3\" | "
                "Select-Object DeviceID,Size,FreeSpace | Format-List | Out-String",
            ),
            "load": (
                "查看系统负载",
                "Get-CimInstance Win32_Processor | Select-Object LoadPercentage | "
                "Format-List | Out-String",
            ),
            "mem": (
                "查看可用内存",
                "Get-CimInstance Win32_OperatingSystem | Select-Object "
                "@{N='TotalGB';E={[math]::Round($_.TotalVisibleMemorySize/1MB,2)}},"
                "@{N='FreeGB';E={[math]::Round($_.FreePhysicalMemory/1MB,2)}} | "
                "ConvertTo-Json -Compress",
            ),
        }
    return {
        "proc_mem": (
            "查看内存占用最高的进程",
            "ps aux --sort=-%mem | head -n 15",
        ),
        "hostname": ("查看主机名", "hostname"),
        "disk": ("查看磁盘空间", "df -h"),
        "load": ("查看系统负载", "uptime"),
        "mem": ("查看可用内存", "free -h"),
    }


def classify_readonly_intent(
    text: str,
    *,
    conn_type: str = "ssh",
) -> Optional[ReadonlyIntentHit]:
    """严格匹配只读窄问；不命中返回 None（由上层澄清或走 LLM）。"""
    q = normalize_nl_query(text)
    if not q:
        return None

    table = _cmds_for(conn_type)

    # 顺序固定：进程占内存 → 主机名 → 磁盘 → 负载 → 可用内存
    if _PROC_MEM_RE.search(q):
        label, cmd = table["proc_mem"]
        return ReadonlyIntentHit(label=label, command=cmd, kind="proc_mem")
    if _HOSTNAME_RE.search(q):
        label, cmd = table["hostname"]
        return ReadonlyIntentHit(label=label, command=cmd, kind="hostname")
    if _DISK_RE.search(q):
        label, cmd = table["disk"]
        return ReadonlyIntentHit(label=label, command=cmd, kind="disk")
    if _LOAD_RE.search(q) and not re.search(r"(?i)内存|memory", q):
        label, cmd = table["load"]
        return ReadonlyIntentHit(label=label, command=cmd, kind="load")
    if _MEM_FREE_RE.search(q):
        label, cmd = table["mem"]
        return ReadonlyIntentHit(label=label, command=cmd, kind="mem")
    return None
