"""多机只读诊断：命令模板 + 结果聚合（供编排 / 工具层调用）。

执行层由调用方注入（ssh_batch / winrm_batch / oneshot），本模块不直接连主机。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence


DIAG_TOOLS = (
    "process_list",
    "service_status",
    "log_search",
    "network_connections",
)


@dataclass
class HostDiagRaw:
    host_id: str
    host_label: str = ""
    ok: bool = False
    stdout: str = ""
    error: str = ""
    conn_type: str = "ssh"


@dataclass
class AggregatedDiagResult:
    tool: str
    total_hosts: int = 0
    successful_hosts: List[str] = field(default_factory=list)
    failed_hosts: List[str] = field(default_factory=list)
    per_host: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "total_hosts": self.total_hosts,
            "successful_hosts": list(self.successful_hosts),
            "failed_hosts": list(self.failed_hosts),
            "per_host": dict(self.per_host),
            "summary": dict(self.summary),
        }


def diag_command(
    tool: str,
    *,
    conn_type: str = "ssh",
    sort_by: str = "cpu",
    limit: int = 8,
    pattern: str = "",
) -> str:
    """按 OS/连接类型返回只读诊断命令。"""
    tool_n = (tool or "").strip().lower()
    ct = (conn_type or "ssh").strip().lower()
    is_win = ct == "winrm"
    lim = max(3, min(30, int(limit or 8)))
    if tool_n == "process_list":
        if is_win:
            sort = "CPU" if (sort_by or "cpu").lower() != "mem" else "WS"
            return (
                f"Get-Process | Sort-Object {sort} -Descending | "
                f"Select-Object -First {lim} Name,Id,CPU,WS | Format-Table -AutoSize | Out-String -Width 200"
            )
        sort_key = "-%mem" if (sort_by or "cpu").lower() == "mem" else "-%cpu"
        return f"ps aux --sort={sort_key} | head -n {lim + 1}"
    if tool_n == "service_status":
        if is_win:
            return (
                "Get-Service | Where-Object { $_.Status -ne 'Running' } | "
                "Select-Object -First 20 Name,Status,StartType | Format-Table -AutoSize | Out-String -Width 200"
            )
        return (
            "systemctl list-units --type=service --state=failed --no-pager --no-legend 2>/dev/null; "
            "echo '---'; "
            "systemctl list-units --type=service --state=running --no-pager --no-legend 2>/dev/null | head -n 15"
        )
    if tool_n == "log_search":
        q = (pattern or "error").strip()[:80] or "error"
        if is_win:
            return (
                f"Get-WinEvent -LogName System -MaxEvents 40 -ErrorAction SilentlyContinue | "
                f"Where-Object {{ $_.Message -match '{q}' }} | Select-Object -First 12 "
                f"TimeCreated,Id,LevelDisplayName,Message | Format-List | Out-String -Width 200"
            )
        return (
            f"journalctl -n 80 --no-pager 2>/dev/null | grep -iE '{re.escape(q)}' | tail -n 20 "
            f"|| dmesg 2>/dev/null | grep -iE '{re.escape(q)}' | tail -n 20 "
            f"|| echo '(no matching log lines)'"
        )
    if tool_n == "network_connections":
        if is_win:
            return (
                "Get-NetTCPConnection -State Listen,Established -ErrorAction SilentlyContinue | "
                "Select-Object -First 25 LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess | "
                "Format-Table -AutoSize | Out-String -Width 200"
            )
        return "ss -tunap 2>/dev/null | head -n 40 || netstat -tunap 2>/dev/null | head -n 40"
    raise ValueError(f"unsupported diag tool: {tool}")


def parse_ps_aux_lines(stdout: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    """粗解析 ``ps aux`` 表头行后的进程。"""
    lines = [ln.rstrip() for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return []
    # 跳过表头
    body = lines[1:] if re.search(r"\bPID\b|\bUSER\b", lines[0], re.I) else lines
    out: List[Dict[str, Any]] = []
    for ln in body[:limit]:
        parts = ln.split(None, 10)
        if len(parts) < 11:
            continue
        try:
            cpu = float(parts[2])
        except Exception:
            cpu = 0.0
        try:
            mem = float(parts[3])
        except Exception:
            mem = 0.0
        cmd = parts[10]
        name = cmd.split()[0].rsplit("/", 1)[-1] if cmd else "?"
        out.append(
            {
                "user": parts[0],
                "pid": parts[1],
                "cpu_percent": cpu,
                "mem_percent": mem,
                "name": name[:64],
                "command": cmd[:160],
            }
        )
    return out


def aggregate_process_results(
    raw_results: Sequence[HostDiagRaw],
    *,
    limit: int = 8,
) -> AggregatedDiagResult:
    """将多台主机 process_list 原始输出聚合成横向对比结构。"""
    result = AggregatedDiagResult(tool="process_list", total_hosts=len(raw_results))
    cpu_values: List[float] = []
    name_counter: Dict[str, int] = {}
    max_cpu = 0.0
    max_host = ""
    avg_top = 0.0

    for raw in raw_results:
        hid = raw.host_id
        label = raw.host_label or hid
        if not raw.ok:
            result.failed_hosts.append(hid)
            result.per_host[hid] = {
                "ok": False,
                "label": label,
                "error": (raw.error or "failed")[:300],
                "top_processes": [],
            }
            continue
        result.successful_hosts.append(hid)
        parsed = parse_ps_aux_lines(raw.stdout, limit=limit)
        # WinRM Format-Table：无法可靠解析时用首行摘要
        if not parsed and raw.stdout.strip():
            snippet = raw.stdout.strip().splitlines()[:6]
            result.per_host[hid] = {
                "ok": True,
                "label": label,
                "top_processes": [],
                "raw_preview": "\n".join(snippet)[:800],
            }
            continue
        top_cpu = parsed[0]["cpu_percent"] if parsed else 0.0
        cpu_values.append(top_cpu)
        if top_cpu > max_cpu:
            max_cpu = top_cpu
            max_host = label
        for p in parsed:
            nm = str(p.get("name") or "")
            if nm:
                name_counter[nm] = name_counter.get(nm, 0) + 1
        result.per_host[hid] = {
            "ok": True,
            "label": label,
            "top_cpu": top_cpu,
            "top_processes": parsed[:5],
        }

    if cpu_values:
        avg_top = sum(cpu_values) / len(cpu_values)
    ok_n = len(result.successful_hosts)
    threshold = max(2, int(ok_n * 0.8)) if ok_n else 99
    common = [
        name
        for name, count in sorted(name_counter.items(), key=lambda x: (-x[1], x[0]))
        if count >= threshold
    ][:10]
    outliers: List[Dict[str, Any]] = []
    if avg_top > 0 and ok_n >= 2:
        for hid in result.successful_hosts:
            ph = result.per_host.get(hid) or {}
            top = float(ph.get("top_cpu") or 0)
            if top >= avg_top * 1.35 and top - avg_top >= 8:
                outliers.append(
                    {
                        "host_id": hid,
                        "label": ph.get("label") or hid,
                        "top_cpu": top,
                        "delta": round(top - avg_top, 1),
                    }
                )
    result.summary = {
        "avg_top_cpu": round(avg_top, 1),
        "max_cpu_host": max_host or None,
        "max_cpu_value": round(max_cpu, 1),
        "common_processes": common,
        "outliers": outliers,
    }
    return result


def aggregate_generic_results(
    tool: str,
    raw_results: Sequence[HostDiagRaw],
    *,
    preview_lines: int = 12,
) -> AggregatedDiagResult:
    """其余诊断工具的轻量聚合：成功/失败 + 短摘要。"""
    result = AggregatedDiagResult(tool=tool, total_hosts=len(raw_results))
    for raw in raw_results:
        hid = raw.host_id
        label = raw.host_label or hid
        if not raw.ok:
            result.failed_hosts.append(hid)
            result.per_host[hid] = {
                "ok": False,
                "label": label,
                "error": (raw.error or "failed")[:300],
            }
            continue
        result.successful_hosts.append(hid)
        lines = [ln for ln in (raw.stdout or "").splitlines() if ln.strip()]
        result.per_host[hid] = {
            "ok": True,
            "label": label,
            "raw_preview": "\n".join(lines[:preview_lines])[:1200],
            "line_count": len(lines),
        }
    result.summary = {
        "ok_count": len(result.successful_hosts),
        "fail_count": len(result.failed_hosts),
    }
    return result


def build_multihost_prompt_block(
    *,
    display_name: str,
    host_labels: Sequence[str],
    multi: bool,
) -> str:
    """注入 Hermes / 编排的多机排查上下文。"""
    labels = [str(x).strip() for x in host_labels if str(x).strip()]
    n = len(labels)
    lines = [
        "[多机排查上下文]",
        f"当前目标范围：{display_name}",
        f"主机数量：{n} 台",
        "主机清单：",
    ]
    for lab in labels[:40]:
        lines.append(f"  - {lab}")
    if not multi:
        lines.append(
            "\n[分析要求]\n当前仅 1 台主机：按单机排查输出即可，无需横向对比四段结构。"
        )
        return "\n".join(lines)
    lines.append(
        """
[分析要求]
用户发起的排查请求涉及多台主机。请按以下格式输出：

1. **总体态势**：一句话概括整体状态，包含关键数值范围
2. **共性特征**：所有（或绝大多数）主机共同表现出的现象
3. **异常离群点**：个别主机独有的异常（如有）；明显偏高时高亮主机名
4. **根因建议**：最可能的根因 + 可执行的修复方案

禁止行为：
- 不要逐台罗列详细输出
- 不要重复输出相同的命令结果
- 如果证据不足，明确说明「需要进一步检查 X」
""".strip()
    )
    return "\n".join(lines)


def detect_single_host_followup(
    text: str,
    host_labels_by_id: Dict[str, str],
) -> Optional[str]:
    """若用户追问点名单机，返回 host_id。"""
    body = (text or "").strip().lower()
    if not body or not host_labels_by_id:
        return None
    # 优先匹配较长标签，避免短名误伤
    items = sorted(
        host_labels_by_id.items(),
        key=lambda kv: (-len(str(kv[1] or "")), -len(str(kv[0] or ""))),
    )
    for hid, label in items:
        for tok in (label, hid):
            t = str(tok or "").strip().lower()
            if t and t in body:
                return hid
    return None


BatchRunner = Callable[[str, Sequence[str], str], Dict[str, HostDiagRaw]]


def run_diag_aggregated(
    tool: str,
    host_metas: Sequence[Dict[str, Any]],
    *,
    runner: BatchRunner,
    sort_by: str = "cpu",
    limit: int = 8,
    pattern: str = "",
) -> AggregatedDiagResult:
    """按 conn_type 分组取命令，经 runner 执行后聚合。

    runner(tool_or_conn_key, host_ids, command) -> {host_id: HostDiagRaw}
    简化：调用方也可自行批跑后直接调 aggregate_*。
    """
    tool_n = (tool or "").strip().lower()
    if tool_n not in DIAG_TOOLS:
        raise ValueError(f"unsupported diag tool: {tool}")
    groups: Dict[str, List[Dict[str, Any]]] = {"ssh": [], "winrm": []}
    for m in host_metas:
        ct = str(m.get("conn_type") or "ssh").lower()
        key = "winrm" if ct == "winrm" else "ssh"
        groups[key].append(m)
    raws: List[HostDiagRaw] = []
    for ct, members in groups.items():
        if not members:
            continue
        cmd = diag_command(
            tool_n, conn_type=ct, sort_by=sort_by, limit=limit, pattern=pattern
        )
        ids = [str(m.get("host_id") or m.get("id") or "") for m in members]
        ids = [x for x in ids if x]
        partial = runner(ct, ids, cmd) or {}
        for m in members:
            hid = str(m.get("host_id") or m.get("id") or "")
            if not hid:
                continue
            if hid in partial:
                raws.append(partial[hid])
            else:
                raws.append(
                    HostDiagRaw(
                        host_id=hid,
                        host_label=str(m.get("name") or hid),
                        ok=False,
                        error="no_result",
                        conn_type=ct,
                    )
                )
    if tool_n == "process_list":
        return aggregate_process_results(raws, limit=limit)
    return aggregate_generic_results(tool_n, raws)
