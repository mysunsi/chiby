"""风险 / 变更判定正则与规则基类（开源护栏契约）。

从 orchestrator / confirm_card_meta 下沉，供确认卡与编排共用，避免反向依赖。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern


@dataclass(frozen=True)
class RiskRule:
    """一条风险规则：名称 + 级别 + 正则。"""

    name: str
    level: str  # blocked | high | medium | low
    pattern: Pattern[str]


# ── 变更类命令（orchestrator 原 _MUTATE_CMD_RE 等）──────────────────────────

MUTATE_CMD_RE = re.compile(
    r"(?i)\b(restart|stop|reload|reboot|shutdown|rm\s|kill\b|taskkill\b|"
    r"Stop-Process|Stop-Service|Restart-Service|Remove-Item|Clear-Item|"
    r"chmod\b|chown\b|chgrp\b|setfacl\b|usermod\b|userdel\b|useradd\b|"
    r"deluser\b|adduser\b|groupdel\b|groupadd\b|"
    r"Remove-LocalUser|New-LocalUser|"
    r"vacuum|truncate|dd\s|"
    r"systemctl\s+(?:restart|stop|reload|start|enable|disable|mask|unmask|"
    r"kill|daemon-reload)\b|"
    r"sc(?:\.exe)?\s+(?:stop|delete|config)\b)",
)

CONTROLLED_MUTATE_RE = re.compile(
    r"(?i)\b("
    r"taskkill\b|Stop-Process\b|Stop-Service\b|Restart-Service\b|"
    r"Remove-Item\b|Clear-Item\b|Clear-DnsClientCache\b|Clear-RecycleBin\b|"
    r"Restart-Computer\b|Stop-Computer\b|"
    r"systemctl\s+(?:restart|stop|reload|start)\b|"
    r"nginx\s+-s\s+reload\b|"
    r"(?:sudo\s+)?(?:chmod|chown|chgrp|setfacl)\b|"
    r"(?:sudo\s+)?rm\b|"
    r"(?:sudo\s+)?(?:userdel|deluser|useradd|adduser|usermod|"
    r"groupdel|groupadd|groupmod|gpasswd)\b|"
    r"Remove-LocalUser\b|New-LocalUser\b|Set-LocalUser\b|"
    r"Remove-LocalGroup\b|New-LocalGroup\b|"
    r"net\s+user\b|net\s+localgroup\b|"
    r"sc(?:\.exe)?\s+stop\b"
    r")",
)

REMOTE_WRITE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:tee|touch|install|rsync|scp)\b|"
    r"\b(?:cp|mv|mkdir)\b|"
    r"\b(?:Set-Content|Add-Content|Out-File|New-Item|Copy-Item|Move-Item)\b|"
    r"\b(?:pip3?|npm|pnpm|yarn)\s+(?:install|uninstall|add|remove|ci)\b|"
    r"\b(?:cargo|go)\s+(?:build|install|get|mod)\b|"
    r"\b(?:make|cmake|gcc|g\+\+|dotnet|mvn|gradle)\b|"
    r"\bpython3?\s+-m\s+pip\b|"
    r"(?:^|[\s;|&])(?:\d*)>>(?!&)\s*\S|"
    r"(?:^|[\s;|&])(?:\d*)>(?!>&)\s*(?!/dev/null\b)(?!nul\b)\S|"
    r"<<\s*"
    r")",
)

BLOCKED_MUTATE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:reboot|shutdown|diskpart|Reset-Computer|Remove-Computer)\b|"
    r"\brm\s+(?:-[a-zA-Z]+)*\s*(?:--no-preserve-root\s+)?/|"
    r"\bdd\s+if=|"
    r"\bcipher\s+/w\b|"
    r"\bformat(?:\.com)?\s+[A-Z]:"
    r")",
)

OPS_HIGH_RISK_DELETE_RE = re.compile(
    r"(?i)\b(?:rm\b|Remove-Item\b|Clear-Item\b|unlink\b|"
    r"del\s+|erase\s+|rd\s+/s\b|rmdir\s+/s\b)"
)
OPS_HIGH_RISK_STOP_RE = re.compile(
    r"(?i)\bsystemctl\s+stop\b|Stop-Service\b|Stop-Process\b|"
    r"taskkill\b|(?:^|[\s;|&])kill\b"
)
OPS_HIGH_RISK_ACCOUNT_RE = re.compile(
    r"(?i)\b(?:"
    r"userdel|deluser|useradd|adduser|usermod|"
    r"groupdel|groupadd|groupmod|gpasswd|"
    r"Remove-LocalUser|New-LocalUser|Set-LocalUser|"
    r"Remove-LocalGroup|New-LocalGroup|"
    r"Remove-ADUser|New-ADUser|Set-ADUser|"
    r"net\s+user\b|"
    r"net\s+localgroup\b"
    r")\b"
)

# ── 确认卡操作类型推断（confirm_card_meta 原私有正则）───────────────────────

REMOVE_RE = re.compile(
    r"(?i)\b(?:rm\b|Remove-Item\b|remote_remove\b|unlink\b|del\s+)",
)
WRITE_RE = re.compile(
    r"(?i)\b(?:remote_write_file|tee\b|Set-Content|Out-File|sed\s+-i\b)|"
    r"(?:^|[\s;|&])(?:\d*)>>?(?!&)\s*\S",
)
RESTART_RE = re.compile(
    r"(?i)\bsystemctl\s+restart\b|Restart-Service\b|nginx\s+-s\s+reload\b",
)
STOP_RE = re.compile(
    r"(?i)\bsystemctl\s+stop\b|Stop-Service\b|Stop-Process\b|taskkill\b|\bkill\b",
)
START_RE = re.compile(
    r"(?i)\bsystemctl\s+start\b|Start-Service\b",
)
CHMOD_RE = re.compile(r"(?i)\b(?:chmod|chown|chgrp|setfacl)\b")
MKDIR_RE = re.compile(r"(?i)\b(?:mkdir|remote_mkdir|New-Item\b.*-ItemType\s+Directory)")
RESTORE_RE = re.compile(r"(?i)\b(?:remote_restore|remote_rollback)\b")
FIREWALL_RE = re.compile(
    r"(?i)\b(?:iptables|ufw|firewall-cmd|netsh\s+advfirewall)\b",
)
CRITICAL_PATH_RE = re.compile(
    r"(?i)(?:/etc/fstab|/etc/sudoers|/boot/|/etc/passwd|/etc/shadow)",
)
PATH_RE = re.compile(r"(?P<p>/(?:[\w.\-]+/)*[\w.\-]+|[A-Za-z]:\\(?:[^\s\"']+))")

# 兼容旧名（confirm_card / 文档提及 RISK_PATTERNS）
RISK_PATTERNS: tuple[RiskRule, ...] = (
    RiskRule("blocked_mutate", "blocked", BLOCKED_MUTATE_RE),
    RiskRule("high_delete", "high", OPS_HIGH_RISK_DELETE_RE),
    RiskRule("high_stop", "high", OPS_HIGH_RISK_STOP_RE),
    RiskRule("high_account", "high", OPS_HIGH_RISK_ACCOUNT_RE),
    RiskRule("firewall", "high", FIREWALL_RE),
    RiskRule("critical_path", "high", CRITICAL_PATH_RE),
    RiskRule("mutate", "medium", MUTATE_CMD_RE),
)


def ops_cmd_is_high_risk(cmd: str) -> bool:
    """运维/高效型「高危」：须弹确认（或直接拦截）。

    纯规则判定；``command_line_danger`` 惰性导入，避免与 ``llm_shell`` 循环依赖。
    """
    s = (cmd or "").strip()
    if not s:
        return False
    if BLOCKED_MUTATE_RE.search(s):
        return True
    if OPS_HIGH_RISK_DELETE_RE.search(s):
        return True
    if OPS_HIGH_RISK_STOP_RE.search(s):
        return True
    if OPS_HIGH_RISK_ACCOUNT_RE.search(s):
        return True
    if re.search(r"(?i)\b(?:remote_restore|remote_rollback)\b", s):
        return True
    from chibyterm.llm_shell import command_line_danger

    danger, _ = command_line_danger(s)
    return bool(danger)
