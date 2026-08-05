"""Linux 发行版探测与命令族映射（确定性，不交给 LLM）。

设计见 docs/linux-distro-command-profile-design.md。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DISTRO_FAMILIES = frozenset(
    {"debian", "rhel", "suse", "alpine", "arch", "linux_generic"}
)

# 单次 SSH exec：KEY=VAL 输出，短超时友好
DISTRO_PROBE_SCRIPT = r"""
set +e
if [ -r /etc/os-release ]; then . /etc/os-release; fi
echo "PRETTY_NAME=${PRETTY_NAME-}"
echo "ID=${ID-}"
echo "ID_LIKE=${ID_LIKE-}"
echo "VERSION_ID=${VERSION_ID-}"
command -v systemctl >/dev/null 2>&1 && echo "HAS_SYSTEMCTL=1" || echo "HAS_SYSTEMCTL=0"
command -v rc-service >/dev/null 2>&1 && echo "HAS_OPENRC=1" || echo "HAS_OPENRC=0"
command -v apt-get >/dev/null 2>&1 && echo "HAS_APT=1" || echo "HAS_APT=0"
command -v dnf >/dev/null 2>&1 && echo "HAS_DNF=1" || echo "HAS_DNF=0"
command -v yum >/dev/null 2>&1 && echo "HAS_YUM=1" || echo "HAS_YUM=0"
command -v apk >/dev/null 2>&1 && echo "HAS_APK=1" || echo "HAS_APK=0"
command -v zypper >/dev/null 2>&1 && echo "HAS_ZYPPER=1" || echo "HAS_ZYPPER=0"
command -v pacman >/dev/null 2>&1 && echo "HAS_PACMAN=1" || echo "HAS_PACMAN=0"
echo "UNAME_S=$(uname -s 2>/dev/null)"
echo "UNAME_M=$(uname -m 2>/dev/null)"
exit 0
""".strip()

DEFAULT_PROBE_TTL_DAYS = 14


class DistroProfile(BaseModel):
    """主机发行版命令族指纹。"""

    family: str = "linux_generic"
    id_like: List[str] = Field(default_factory=list)
    pretty_name: str = ""
    id: str = ""
    version_id: str = ""
    pkg_manager: str = "unknown"
    init_system: str = "unknown"
    probed_at: Optional[str] = None
    probe_source: str = "ssh_oneshot"  # ssh_oneshot | session_connect | manual
    stale: bool = False
    uname_s: str = ""
    uname_m: str = ""

    def mark_stale_if_expired(self, *, ttl_days: int = DEFAULT_PROBE_TTL_DAYS) -> "DistroProfile":
        if self.probe_source == "manual":
            return self.model_copy(update={"stale": False})
        if not self.probed_at:
            return self.model_copy(update={"stale": True})
        try:
            raw = self.probed_at.replace("Z", "+00:00")
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
            if age > timedelta(days=max(1, int(ttl_days))):
                return self.model_copy(update={"stale": True})
        except Exception:
            return self.model_copy(update={"stale": True})
        return self.model_copy(update={"stale": False})


def _parse_kv_lines(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def parse_probe_stdout(stdout: str) -> Dict[str, Any]:
    """把探测脚本 stdout 解析为中间结构。"""
    kv = _parse_kv_lines(stdout)
    id_like_raw = kv.get("ID_LIKE", "") or ""
    id_like = [x.strip().lower() for x in re.split(r"[\s,]+", id_like_raw) if x.strip()]
    return {
        "pretty_name": kv.get("PRETTY_NAME", "") or "",
        "id": (kv.get("ID", "") or "").strip().lower(),
        "id_like": id_like,
        "version_id": kv.get("VERSION_ID", "") or "",
        "has_systemctl": kv.get("HAS_SYSTEMCTL", "0") == "1",
        "has_openrc": kv.get("HAS_OPENRC", "0") == "1",
        "has_apt": kv.get("HAS_APT", "0") == "1",
        "has_dnf": kv.get("HAS_DNF", "0") == "1",
        "has_yum": kv.get("HAS_YUM", "0") == "1",
        "has_apk": kv.get("HAS_APK", "0") == "1",
        "has_zypper": kv.get("HAS_ZYPPER", "0") == "1",
        "has_pacman": kv.get("HAS_PACMAN", "0") == "1",
        "uname_s": kv.get("UNAME_S", "") or "",
        "uname_m": kv.get("UNAME_M", "") or "",
    }


def map_facts_to_profile(
    facts: Dict[str, Any],
    *,
    probe_source: str = "ssh_oneshot",
    probed_at: Optional[str] = None,
) -> DistroProfile:
    """确定性映射：包管理器实装优先于 ID 字符串。"""
    fid = str(facts.get("id") or "").lower()
    id_like = [str(x).lower() for x in (facts.get("id_like") or [])]
    blob = " ".join([fid] + id_like)

    has_apk = bool(facts.get("has_apk"))
    has_apt = bool(facts.get("has_apt"))
    has_dnf = bool(facts.get("has_dnf"))
    has_yum = bool(facts.get("has_yum"))
    has_zypper = bool(facts.get("has_zypper"))
    has_pacman = bool(facts.get("has_pacman"))
    has_systemctl = bool(facts.get("has_systemctl"))
    has_openrc = bool(facts.get("has_openrc"))

    family = "linux_generic"
    pkg = "unknown"

    # Alpine：apk 且（ID=alpine 或无 systemctl）
    if has_apk and (fid == "alpine" or "alpine" in blob or not has_systemctl):
        family, pkg = "alpine", "apk"
    elif has_zypper and (
        fid in ("opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "suse")
        or "suse" in blob
        or not (has_apt or has_dnf)
    ):
        family, pkg = "suse", "zypper"
    elif has_pacman and (fid in ("arch", "manjaro", "endeavouros") or "arch" in blob):
        family, pkg = "arch", "pacman"
    elif has_apt and (
        fid in ("debian", "ubuntu", "linuxmint", "raspbian", "pop", "kali")
        or any(x in blob for x in ("debian", "ubuntu"))
    ):
        family, pkg = "debian", "apt"
    elif has_dnf or (
        has_yum
        and (
            fid
            in (
                "rhel",
                "centos",
                "rocky",
                "almalinux",
                "fedora",
                "ol",
                "amzn",
            )
            or any(x in blob for x in ("rhel", "fedora", "centos"))
        )
    ):
        family = "rhel"
        pkg = "dnf" if has_dnf else "yum"
    elif has_apk:
        family, pkg = "alpine", "apk"
    elif has_apt:
        family, pkg = "debian", "apt"
    elif has_dnf:
        family, pkg = "rhel", "dnf"
    elif has_yum:
        family, pkg = "rhel", "yum"
    elif has_zypper:
        family, pkg = "suse", "zypper"
    elif has_pacman:
        family, pkg = "arch", "pacman"

    if has_systemctl:
        init = "systemd"
    elif has_openrc:
        init = "openrc"
    else:
        init = "unknown"

    now = probed_at or datetime.now(timezone.utc).isoformat()
    return DistroProfile(
        family=family,
        id_like=id_like,
        pretty_name=str(facts.get("pretty_name") or ""),
        id=fid,
        version_id=str(facts.get("version_id") or ""),
        pkg_manager=pkg,
        init_system=init,
        probed_at=now,
        probe_source=probe_source,
        stale=False,
        uname_s=str(facts.get("uname_s") or ""),
        uname_m=str(facts.get("uname_m") or ""),
    )


def profile_from_probe_stdout(
    stdout: str,
    *,
    probe_source: str = "ssh_oneshot",
) -> DistroProfile:
    facts = parse_probe_stdout(stdout)
    # 完全空输出 → generic
    if not any(
        [
            facts.get("id"),
            facts.get("pretty_name"),
            facts.get("has_apt"),
            facts.get("has_dnf"),
            facts.get("has_apk"),
        ]
    ) and not (stdout or "").strip():
        return DistroProfile(
            family="linux_generic",
            probed_at=datetime.now(timezone.utc).isoformat(),
            probe_source=probe_source,
            stale=True,
        )
    return map_facts_to_profile(facts, probe_source=probe_source)


def coerce_distro_profile(obj: Any) -> Optional[DistroProfile]:
    """Host / dict / DistroProfile → DistroProfile；无法识别则 None。"""
    if obj is None:
        return None
    if isinstance(obj, DistroProfile):
        return obj
    if isinstance(obj, dict):
        try:
            return DistroProfile.model_validate(obj)
        except Exception:
            return None
    return None


def format_distro_preamble_block(profile: Any) -> str:
    """掌上 Hermes preamble 用：有指纹则返回完整命令族段（末尾换行）。"""
    p = coerce_distro_profile(profile)
    if p is None:
        return ""
    return build_distro_runtime_hint(p)


def build_distro_runtime_hint(profile: Optional[DistroProfile]) -> str:
    """注入 LLM 的发行版命令族约束；无指纹则空串。"""
    if profile is None:
        return ""
    p = profile.mark_stale_if_expired()
    family = p.family if p.family in DISTRO_FAMILIES else "linux_generic"
    pretty = (p.pretty_name or p.id or family).strip()
    pkg = p.pkg_manager or "unknown"
    init = p.init_system or "unknown"

    lines = [
        "【发行版命令族 — 必须遵守】",
        f"family={family} pretty={pretty} pkg={pkg} init={init}",
    ]
    if p.stale:
        lines.append("（指纹可能过期；不确定时先用 POSIX 通用命令，装包前确认包管理器）")

    if family == "debian":
        lines.append("- 装包/卸包：apt-get / apt（勿用 yum/dnf/apk）")
        lines.append("- 服务：systemctl；日志：journalctl")
        lines.append("- 防火墙：优先 ufw 或 nft/iptables（勿默认 firewalld）")
    elif family == "rhel":
        pm = "dnf" if pkg == "dnf" else "yum"
        lines.append(f"- 装包/卸包：优先 {pm}（勿用 apt/apk）")
        lines.append("- 服务：systemctl；日志：journalctl")
        lines.append("- 防火墙：优先 firewalld（firewall-cmd）")
    elif family == "alpine":
        lines.append("- 装包/卸包：apk（勿用 apt/yum）")
        lines.append("- 服务：优先 rc-service / OpenRC（勿默认假定 systemctl）")
    elif family == "suse":
        lines.append("- 装包/卸包：zypper（勿用 apt/yum）")
        lines.append("- 服务：systemctl；日志：journalctl")
    elif family == "arch":
        lines.append("- 装包/卸包：pacman（勿用 apt/yum）")
        lines.append("- 服务：systemctl；日志：journalctl")
    else:
        lines.append("- 未识别发行版：优先 POSIX（sh/ps/df）；装包前先 which apt-get/dnf/apk")
        lines.append("- 勿假定 systemctl 一定存在")

    lines.append("- 禁止输出 Windows PowerShell cmdlet")
    return "\n".join(lines) + "\n"


def needs_probe(profile: Optional[DistroProfile], *, ttl_days: int = DEFAULT_PROBE_TTL_DAYS) -> bool:
    if profile is None:
        return True
    if profile.probe_source == "manual" and profile.family in DISTRO_FAMILIES:
        return False
    p = profile.mark_stale_if_expired(ttl_days=ttl_days)
    return bool(p.stale or p.family not in DISTRO_FAMILIES)


def probe_host_distro(host_obj: Any, *, probe_source: str = "ssh_oneshot") -> Tuple[DistroProfile, str]:
    """对 SSH 主机 oneshot 探测。返回 (profile, raw_stdout_or_error)。"""
    from chibycore.executor_contract import RunOptions
    from chibycore.unified_executor_factory import build_oneshot_from_pydantic_host

    ct = getattr(host_obj, "conn_type", "ssh")
    if hasattr(ct, "value"):
        ct = ct.value
    if str(ct).lower() != "ssh":
        raise ValueError("仅 SSH 主机支持发行版探测")

    ex = build_oneshot_from_pydantic_host(host_obj)
    ex.connect()
    try:
        result = ex.run_command(
            DISTRO_PROBE_SCRIPT,
            RunOptions(timeout_sec=15.0),
        )
        stdout = (result.stdout or "") + ("\n" + (result.stderr or "") if result.stderr else "")
        if result.exit_code not in (0, None) and not (result.stdout or "").strip():
            logger.warning(
                "distro probe failed host=%s exit=%s err=%s",
                getattr(host_obj, "id", "?"),
                result.exit_code,
                (result.stderr or "")[:200],
            )
            return (
                DistroProfile(
                    family="linux_generic",
                    probed_at=datetime.now(timezone.utc).isoformat(),
                    probe_source=probe_source,
                    stale=True,
                ),
                stdout or (result.error or "probe failed"),
            )
        return profile_from_probe_stdout(stdout, probe_source=probe_source), stdout
    finally:
        try:
            ex.close()
        except Exception:
            pass
