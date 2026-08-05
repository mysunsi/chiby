"""跨会话 NL 意图广播：主机分段与静态冲突检测（不含 LLM）。

主机标签约定（可选，增强检测精度）：
  init:systemd | init:sysv
  distro:debian | distro:rhel | distro:alpine
  业务分组：如 web-servers（与 Host.tags 任意字符串并存）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


def _norm_tags(tags: Optional[List[str]]) -> List[str]:
    return [str(t).strip().lower() for t in (tags or []) if str(t).strip()]


def _host_segment_key(host: Any) -> str:
    """异构适配分段键：WinRM 独立；SSH 再按 init 风格分。"""
    ct = getattr(host, "conn_type", None)
    conn = getattr(ct, "value", ct) or "ssh"
    conn = str(conn).lower()
    if conn == "winrm":
        return "winrm_powershell"
    tags = set(_norm_tags(getattr(host, "tags", None)))
    if tags & {"init:sysv", "sysv", "init-sysv"}:
        return "ssh_linux_sysv"
    return "ssh_linux_systemd"


def _distro_family(tags: List[str]) -> str:
    tset = set(tags)
    if tset & {"distro:debian", "debian", "ubuntu"}:
        return "debian"
    if tset & {"distro:rhel", "rhel", "centos", "fedora", "rocky", "alma"}:
        return "rhel"
    if tset & {"distro:alpine", "alpine"}:
        return "alpine"
    return "unknown"


def _adapter_label(segment_key: str, sample_tags: List[str]) -> str:
    """人类可读：同源意图、异构适配说明。"""
    fam = _distro_family(sample_tags)
    fam_s = f" · {fam}" if fam != "unknown" else ""
    if segment_key == "winrm_powershell":
        return f"同源意图 · WinRM/PowerShell{fam_s}"
    if segment_key == "ssh_linux_sysv":
        return f"同源意图 · SSH · SysV/init{fam_s}"
    return f"同源意图 · SSH · systemd{fam_s}"


@dataclass
class IntentSegment:
    segment_id: str
    conn_type: str
    shell_profile: str  # unix | powershell
    adapter_label: str
    host_ids: List[str]
    host_names: Dict[str, str] = field(default_factory=dict)
    sample_tags: List[str] = field(default_factory=list)


@dataclass
class ConflictItem:
    severity: str  # error | warning | info
    code: str
    message: str
    related_host_ids: List[str] = field(default_factory=list)


def resolve_hosts_by_tag(all_hosts: Dict[str, Any], tag: str) -> List[Any]:
    """tag 匹配 Host.tags 中的任一项（大小写不敏感）。"""
    want = tag.strip().lower()
    if not want:
        return []
    out: List[Any] = []
    for hid, h in all_hosts.items():
        tags = _norm_tags(getattr(h, "tags", None))
        if want in tags:
            if getattr(h, "is_active", True):
                out.append(h)
    return out


def resolve_hosts_by_ids(all_hosts: Dict[str, Any], ids: List[str]) -> List[Any]:
    out: List[Any] = []
    for i in ids:
        h = all_hosts.get(i)
        if h and getattr(h, "is_active", True):
            out.append(h)
    return out


def resolve_hosts_union(
    all_hosts: Dict[str, Any],
    tag: Optional[str],
    ids: Optional[List[str]],
) -> List[Any]:
    """tag 与 host_ids 同时给出时取并集。"""
    seen: Dict[str, Any] = {}
    if tag and str(tag).strip():
        for h in resolve_hosts_by_tag(all_hosts, str(tag).strip()):
            hid = getattr(h, "id", "")
            if hid:
                seen[hid] = h
    if ids:
        for h in resolve_hosts_by_ids(all_hosts, ids):
            hid = getattr(h, "id", "")
            if hid:
                seen[hid] = h
    return list(seen.values())


def segment_hosts(hosts: List[Any]) -> List[IntentSegment]:
    """按异构类型分段（用于分段翻译 + 并行派发）。"""
    buckets: Dict[str, List[Any]] = {}
    for h in hosts:
        key = _host_segment_key(h)
        buckets.setdefault(key, []).append(h)

    segments: List[IntentSegment] = []
    order = ["ssh_linux_systemd", "ssh_linux_sysv", "winrm_powershell"]
    for seg_id in order:
        bucket = buckets.pop(seg_id, None)
        if not bucket:
            continue
        ct = "winrm" if seg_id == "winrm_powershell" else "ssh"
        sp = "powershell" if seg_id == "winrm_powershell" else "unix"
        tags_merge: List[str] = []
        for x in bucket:
            tags_merge.extend(_norm_tags(getattr(x, "tags", None)))
        sample = sorted(set(tags_merge))[:24]
        hid = [getattr(h, "id", "") for h in bucket]
        names = {getattr(h, "id", ""): getattr(h, "name", "") for h in bucket}
        segments.append(
            IntentSegment(
                segment_id=seg_id,
                conn_type=ct,
                shell_profile=sp,
                adapter_label=_adapter_label(seg_id, sample),
                host_ids=hid,
                host_names=names,
                sample_tags=sample,
            )
        )
    # 未知分段键兜底
    for seg_id, bucket in buckets.items():
        ct = "winrm" if "winrm" in seg_id else "ssh"
        sp = "powershell" if ct == "winrm" else "unix"
        tags_merge = []
        for x in bucket:
            tags_merge.extend(_norm_tags(getattr(x, "tags", None)))
        sample = sorted(set(tags_merge))[:24]
        segments.append(
            IntentSegment(
                segment_id=seg_id,
                conn_type=ct,
                shell_profile=sp,
                adapter_label=_adapter_label(seg_id, sample),
                host_ids=[getattr(h, "id", "") for h in bucket],
                host_names={getattr(h, "id", ""): getattr(h, "name", "") for h in bucket},
                sample_tags=sample,
            )
        )
    return segments


def analyze_static_conflicts(
    hosts: List[Any],
    segments: List[IntentSegment],
    nl_intent: str,
) -> Tuple[List[ConflictItem], bool]:
    """
    静态冲突检测。返回 (conflicts, dispatch_allowed)。
    dispatch_allowed：仅当 hosts 非空且无 error 级冲突时为 True。
    """
    conflicts: List[ConflictItem] = []
    nl = (nl_intent or "").lower()

    if not hosts:
        conflicts.append(
            ConflictItem(
                severity="error",
                code="no_hosts",
                message="未解析到任何主机（检查 tag 或 host_ids）",
            )
        )
        return conflicts, False

    seg_keys = {s.segment_id for s in segments}
    has_winrm = any(s.segment_id == "winrm_powershell" for s in segments)
    has_ssh = any(s.segment_id.startswith("ssh") for s in segments)

    if has_winrm and has_ssh:
        conflicts.append(
            ConflictItem(
                severity="warning",
                code="mixed_transport",
                message="同一批次同时包含 SSH(Linux) 与 WinRM：将按分段生成不同命令并并行下发（异构适配）。",
                related_host_ids=_all_host_ids(hosts),
            )
        )

    if "ssh_linux_systemd" in seg_keys and "ssh_linux_sysv" in seg_keys:
        conflicts.append(
            ConflictItem(
                severity="warning",
                code="mixed_init_style",
                message="主机间 init 风格不一致（systemd vs SysV）：服务启停类意图将分段翻译，请勿混用单一命令路径。",
                related_host_ids=_all_host_ids(hosts),
            )
        )

    # NL 含 systemctl / journalctl 却包含 WinRM 主机
    if has_winrm and any(k in nl for k in ("systemctl", "journalctl", "apt ", "dpkg ", "yum ", "dnf ")):
        win_ids: List[str] = []
        for s in segments:
            if s.segment_id == "winrm_powershell":
                win_ids.extend(s.host_ids)
        if win_ids:
            low_nl = nl
            hint = ""
            if "systemctl" in low_nl or "journalctl" in low_nl:
                hint = "意图含 systemd 命令，Windows 段将尝试映射为 PowerShell/服务 cmdlet。"
            if "apt" in low_nl or "dpkg" in low_nl:
                hint = "意图含 Debian 包管理用语，仅适用于 Linux 分段。"
            if hint:
                conflicts.append(
                    ConflictItem(
                        severity="info",
                        code="nl_linux_hint_with_windows",
                        message=hint,
                        related_host_ids=win_ids,
                    )
                )

    # 标注 distro 不一致且 NL 明确提 apt/yum
    families: Set[str] = set()
    for h in hosts:
        families.add(_distro_family(_norm_tags(getattr(h, "tags", None))))
    families.discard("unknown")
    if len(families) > 1:
        if "apt" in nl or "apt-get" in nl:
            conflicts.append(
                ConflictItem(
                    severity="warning",
                    code="nl_apt_multi_distro",
                    message="批次内存在多种发行版画像（标签 distro:*），使用 apt 类意图可能对部分主机不适用。",
                    related_host_ids=_all_host_ids(hosts),
                )
            )
        if "yum" in nl or "dnf" in nl:
            conflicts.append(
                ConflictItem(
                    severity="warning",
                    code="nl_yum_multi_distro",
                    message="批次内存在多种发行版画像，yum/dnf 仅适用于 RHEL 系主机。",
                    related_host_ids=_all_host_ids(hosts),
                )
            )

    err = any(c.severity == "error" for c in conflicts)
    return conflicts, not err


def _all_host_ids(hosts: List[Any]) -> List[str]:
    return [getattr(h, "id", "") for h in hosts if getattr(h, "id", None)]


def segments_to_jsonable(segments: List[IntentSegment]) -> List[Dict[str, Any]]:
    out = []
    for s in segments:
        out.append(
            {
                "segment_id": s.segment_id,
                "conn_type": s.conn_type,
                "shell_profile": s.shell_profile,
                "adapter_label": s.adapter_label,
                "host_ids": s.host_ids,
                "host_names": s.host_names,
                "sample_tags": s.sample_tags,
            }
        )
    return out


def conflicts_to_jsonable(rows: List[ConflictItem]) -> List[Dict[str, Any]]:
    return [
        {
            "severity": c.severity,
            "code": c.code,
            "message": c.message,
            "related_host_ids": c.related_host_ids,
        }
        for c in rows
    ]
