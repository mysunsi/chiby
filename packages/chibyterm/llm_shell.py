"""LLM 增强的自然语言 → Shell 命令转换层。"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from chibycore.llm_providers import get_llm, reset_llm_singleton
from chibycore.llm_config import get_effective_llm_settings
from .models import PromptResult
from .shell_context import ShellProfile

logger = logging.getLogger(__name__)


def _estimate_tokens_cn(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


def truncate_chat_messages(
    messages: List[Dict[str, str]],
    max_context_tokens: int,
) -> List[Dict[str, str]]:
    if max_context_tokens <= 0:
        return messages
    system_msgs = [m for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    budget = max_context_tokens
    used = sum(_estimate_tokens_cn(str(m.get("content") or "")) for m in system_msgs)
    kept: List[Dict[str, str]] = []
    for m in reversed(rest):
        chunk = _estimate_tokens_cn(str(m.get("content") or ""))
        if used + chunk <= budget:
            kept.insert(0, m)
            used += chunk
        else:
            break
    return system_msgs + kept


def _resolve_no_think_override(
    eff: Dict[str, Any],
    llm_params: Optional[Dict[str, Any]],
) -> Optional[bool]:
    if not eff.get("allow_thinking"):
        return True
    if not llm_params:
        return None
    if llm_params.get("allow_thinking") is True:
        return False
    if llm_params.get("allow_thinking") is False:
        return True
    if llm_params.get("no_think") is not None:
        return bool(llm_params.get("no_think"))
    return None


def _chat_params_from_settings(overrides: Optional[Dict[str, Any]] = None) -> tuple[float, int]:
    """合并 data/llm_config.json 与单次 WS 请求中的 temperature / max_tokens。"""
    eff = get_effective_llm_settings()
    t = float(eff.get("temperature", 0.1))
    m = int(eff.get("max_tokens", 2048))
    if not overrides:
        return t, m
    try:
        if overrides.get("temperature") is not None:
            t = max(0.0, min(2.0, float(overrides["temperature"])))
    except (TypeError, ValueError):
        pass
    try:
        if overrides.get("max_tokens") is not None:
            m = max(256, min(128000, int(float(overrides["max_tokens"]))))
    except (TypeError, ValueError):
        pass
    return t, m


def looks_like_markdown_analysis(text: str) -> bool:
    """检测误把 LLM 说明/Markdown 当成 Shell 命令的情况。"""
    t = (text or "").strip()
    if not t:
        return False
    lines = [ln.rstrip() for ln in t.splitlines() if ln.strip()]
    if len(lines) >= 2:
        md_hits = 0
        for ln in lines[:12]:
            if ln.startswith("#") or ln.startswith("**") or ln.startswith("```"):
                md_hits += 1
            elif re.match(r"^[-*+]\s+\S", ln):
                md_hits += 1
            elif re.match(r"^[\u4e00-\u9fff].*[：:]\s*$", ln):
                md_hits += 1
            elif "结论" in ln or (len(ln) >= 2 and "分析" in ln[:24]):
                md_hits += 1
        if md_hits >= 2:
            return True
    if re.match(r"^[\u4e00-\u9fffA-Za-z0-9_（）()\s]{2,40}[：:]\s*$", t):
        return True
    if t.startswith("**") and ("**" in t[2:] or t.endswith("**")):
        return True
    # 含 Markdown 强调且几乎不像 shell（无管道/重定向/常见命令词）
    if "**" in t and not re.search(
        r"(?i)\b(free|df|top|ps|ls|cat|systemctl|uptime|w|head|tail|grep|awk|sed|echo|cd|pwd|uname|ip|ss|curl|wget|chmod|chown|mkdir|rm|mv|cp|docker|kubectl|Get-|Set-)\b|[|;&><`$]",
        t,
    ):
        return True
    return False


def looks_like_unsupported_command(cmd: str) -> bool:
    """[COMMAND] 为 UNSUPPORTED / UNSUPPORTED: 原因 时不可执行。"""
    t = (cmd or "").strip()
    if not t:
        return False
    first = t.splitlines()[0].strip()
    u = first.upper()
    return u == "UNSUPPORTED" or u.startswith("UNSUPPORTED:")


def _unsupported_reason_from_command(cmd: str) -> str:
    """从 UNSUPPORTED: 原因 抽出说明；无冒号则空。"""
    t = (cmd or "").strip()
    if not t:
        return ""
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if not lines:
        return ""
    first = lines[0]
    rest = "\n".join(lines[1:]).strip()
    reason = ""
    if ":" in first:
        reason = first.split(":", 1)[1].strip()
    elif "：" in first:
        reason = first.split("：", 1)[1].strip()
    bits = [b for b in (reason, rest) if b]
    return "\n".join(bits).strip()


def sanitize_prompt_result_command(result: PromptResult) -> PromptResult:
    """若 [COMMAND] 实际是分析结果/Markdown/UNSUPPORTED，改写为纯说明，禁止下发执行。"""
    if result is None:
        return PromptResult(should_execute=False)
    cmd = (result.command or "").strip()
    if not cmd:
        result.should_execute = False
        return result
    if looks_like_unsupported_command(cmd):
        extra = _unsupported_reason_from_command(cmd)
        expl = (result.explanation or "").strip()
        if extra:
            if not expl:
                result.explanation = extra
            elif extra not in expl:
                result.explanation = f"{expl}\n\n{extra}".strip()
        elif not expl:
            result.explanation = "无法理解当前输入，未生成可执行命令"
        result.command = ""
        result.should_execute = False
        result.confirm_required = False
        result.is_dangerous = False
        logger.info(
            "sanitized UNSUPPORTED LLM command → explanation only (preview=%r)",
            cmd[:120],
        )
        return result
    if looks_like_markdown_analysis(cmd):
        expl = (result.explanation or "").strip()
        # 把误填的分析挪到说明区，供右侧 Markdown 展示
        if expl and expl not in cmd:
            result.explanation = f"{expl}\n\n{cmd}".strip()
        else:
            result.explanation = cmd
        result.command = ""
        result.should_execute = False
        result.confirm_required = False
        result.is_dangerous = False
        logger.info(
            "sanitized markdown-like LLM command → explanation only (preview=%r)",
            cmd[:120],
        )
        return result
    # 多行命令：剔除像说明的行，保留可执行行
    kept: List[str] = []
    dropped: List[str] = []
    for ln in cmd.splitlines():
        s = ln.strip()
        if not s:
            continue
        if looks_like_unsupported_command(s) or looks_like_markdown_analysis(s) or (
            s.startswith("**")
            or s.startswith("#")
            or re.match(r"^[-*+]\s+\S", s)
            or re.match(r"^[\u4e00-\u9fff].*[：:]\s*$", s)
        ):
            dropped.append(s)
            continue
        kept.append(s)
    if dropped and kept:
        result.command = "\n".join(kept)
        extra = "\n".join(dropped)
        expl = (result.explanation or "").strip()
        result.explanation = f"{expl}\n\n{extra}".strip() if expl else extra
        logger.info(
            "stripped %d analysis line(s) from LLM command",
            len(dropped),
        )
    elif dropped and not kept:
        result.explanation = (
            ((result.explanation or "").strip() + "\n\n" + "\n".join(dropped)).strip()
            if (result.explanation or "").strip()
            else "\n".join(dropped)
        )
        result.command = ""
        result.should_execute = False
        result.confirm_required = False
    if not (result.command or "").strip():
        result.should_execute = False
    return result


def _llm_timeout_hint(exc: BaseException) -> str:
    low = str(exc).lower()
    if "timed out" in low or "timeout" in low:
        return (
            " — 多为 HTTP 读超时：可设置环境变量 LLM_HTTP_TIMEOUT（秒，15～600）"
            "或在 data/llm_models.json / llm_config.json 中增加 http_timeout_sec 后保存并重载 LLM；"
            "本地 Ollama / 大模型推理慢时建议 ≥180。"
        )
    return ""


# ─── 危险命令模式 ────────────────────────────────────────────────────────────

DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b",
    # PowerShell 对等：递归删除 ≈ rm -rf（单文件 Remove-Item -Force 不在此列）
    # 注意：-Recurse 前不能用 \b（连字符前无词界）
    r"\bRemove-Item\b[\s\S]{0,240}-Recurse\b",
    r"\bshutdown\b",
    r"\bRestart-Computer\b",
    r"\bStop-Computer\b",
    r"\binit\s+0\b",
    r"\binit\s+6\b",
    r"\breboot\b",
    r"\bdd\b.*of=/",
    r"\bmkfs\b",
    r"\bdrop\s+database\b",
    r"\btruncate\b.*table\b",
    r"\bkill\s+-9\b",
    r"\b:\(\)\{.*:\|:&\};:\b",
    r"\becho\s+.*>\s*/dev/sd",
    r"\bsed\s+-i\s+.*root\b",
    r"\bchmod\s+777\s+/etc\b",
    r"\bpasswd\b\s+root\b",
    r"\bsudo\s+su\b",
    r"\bcurl\b.*\|\s*bash",
    r"\bwget\b.*\|\s*bash",
    r"\bpython.*-m\s+http\.server\b.*0\.0\.0\.0:80\b",
]

DANGEROUS_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in DANGEROUS_PATTERNS]


def command_line_danger(command: str) -> tuple:
    """单行命令危险检测。返回 (是否危险, 警告文案)。"""
    if not command or not command.strip():
        return False, ""
    for pattern in DANGEROUS_PATTERNS_COMPILED:
        if pattern.search(command):
            return True, f"⚠️ 检测到危险操作: {command.strip()[:60]}"
    return False, ""


# 变更/写入类（至少 MEDIUM，须确认；未命中高危正则时）
# 与掌上机房受控变更思路对齐，但独立实现以免循环依赖。
_MUTATING_CMD_RE = re.compile(
    r"(?i)\b("
    r"rm\b|unlink\b|shred\b|truncate\b|"
    r"mv\b|cp\b|install\b|rsync\b|scp\b|"
    r"mkdir\b|rmdir\b|touch\b|tee\b|"
    r"chmod\b|chown\b|chgrp\b|setfacl\b|"
    r"useradd\b|userdel\b|usermod\b|adduser\b|deluser\b|"
    r"groupadd\b|groupdel\b|groupmod\b|gpasswd\b|"
    r"passwd\b|chpasswd\b|"
    r"kill\b|pkill\b|killall\b|taskkill\b|"
    r"systemctl\s+(?:restart|stop|reload|start|enable|disable|mask|unmask|kill|daemon-reload)\b|"
    r"service\s+\S+\s+(?:start|stop|restart|reload)\b|"
    r"nginx\s+-s\s+(?:reload|stop|quit)\b|"
    r"(?:apt|apt-get|yum|dnf|zypper|pacman)\s+(?:install|remove|purge|erase|upgrade|dist-upgrade)\b|"
    r"(?:pip3?|npm|pnpm|yarn)\s+(?:install|uninstall|add|remove|ci)\b|"
    r"docker\s+(?:rm|rmi|stop|kill|run|exec|compose)\b|"
    r"crontab\b|"
    r"sed\s+-i\b|perl\s+-i\b|"
    r"Remove-Item\b|Clear-Item\b|Clear-Content\b|Clear-RecycleBin\b|"
    r"Move-Item\b|Copy-Item\b|New-Item\b|Rename-Item\b|"
    r"Set-Content\b|Add-Content\b|Out-File\b|"
    r"Stop-Process\b|Stop-Service\b|Start-Service\b|Restart-Service\b|"
    r"Restart-Computer\b|Stop-Computer\b|"
    r"Remove-LocalUser\b|New-LocalUser\b|Set-LocalUser\b|"
    r"net\s+user\b|net\s+localgroup\b|"
    r"sc(?:\.exe)?\s+(?:stop|start|delete|config)\b"
    r")"
)

# 写盘重定向（排除 >/dev/null、>nul；避免误伤 awk 的 NR>1）
_WRITE_REDIRECT_RE = re.compile(
    r"(?:^|[\s;|&])(?:\d*)>>(?!&)\s*\S|"
    r"(?:^|[\s;|&])(?:\d*)>(?!>&)\s*(?!/dev/null\b)(?!nul\b)\S"
)


def looks_like_mutating_command(command: str) -> bool:
    """是否像会改系统状态/写文件的命令（非毁灭性高危仍算变更）。"""
    s = (command or "").strip()
    if not s:
        return False
    if _MUTATING_CMD_RE.search(s):
        return True
    if _WRITE_REDIRECT_RE.search(s):
        return True
    return False


def classify_command_risk(command: str) -> tuple:
    """命令风险：HIGH / MEDIUM / LOW，及可选警告。

    - HIGH：命中危险正则（rm -rf、关机、格式化等）
    - MEDIUM：任意变更/写入（含普通 rm、重定向写盘等）→ 须确认
    - LOW：只读查询等 → 可不反复确认、Shell 可自动下发
    """
    lines = [ln.strip() for ln in (command or "").splitlines() if ln.strip()]
    if not lines:
        return "LOW", ""
    level = "LOW"
    warn = ""
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    for line in lines:
        d, w = command_line_danger(line)
        if d:
            if rank["HIGH"] > rank[level]:
                level = "HIGH"
                warn = w or warn
            continue
        if looks_like_mutating_command(line):
            if rank["MEDIUM"] > rank[level]:
                level = "MEDIUM"
                if not warn:
                    warn = "变更操作，执行前请确认"
    return level, warn


def apply_prompt_result_risk(result: PromptResult) -> PromptResult:
    """按命令内容统一写入 is_dangerous / confirm_required。

    变更类至少 MEDIUM（须确认）；高危正则或 LLM 已标危险 → HIGH。
    """
    if result is None:
        return PromptResult(should_execute=False)
    cmd = (result.command or "").strip()
    if not cmd:
        result.is_dangerous = False
        result.confirm_required = False
        return result
    level, warn = classify_command_risk(cmd)
    if result.is_dangerous and level != "HIGH":
        level = "HIGH"
        if not warn:
            warn = (result.warning or "").strip() or "⚠️ 检测到危险操作"
    result.is_dangerous = level == "HIGH"
    result.confirm_required = level in ("MEDIUM", "HIGH")
    if warn and not (result.warning or "").strip():
        result.warning = warn
    return result


# ─── 系统提示词 ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一个运维终端助手。用户通过自然语言描述运维操作，你需要将其转换为准确的可执行命令。

规则：
1. 只返回命令本身，不要额外的解释或 markdown 代码块
2. 命令要简洁、精准，适合直接在终端执行
3. 用户消息中若包含「运行环境」说明，你必须严格遵守：Windows PowerShell 下禁止使用仅适用于 Linux 的命令（如 df、free、systemctl）；类 Unix 终端下不要使用 PowerShell cmdlet
4. 如果用户要求查看信息，优先使用只读命令（Linux：cat, ls, df, ss；Windows：Get-Process, Get-PSDrive 等）
5. 如果用户要修改配置，先给出查看命令，再给出修改命令，分行输出
6. 如果无法理解用户意图，返回 "UNSUPPORTED: 原因"
7. **严禁**把分析结果、结论、Markdown（**粗体**、列表、- 开头说明、标题「xxx分析：」等）写进 [COMMAND]
8. 若用户只要你根据「终端最近输出」做解读/总结/判断（资源是否紧张等），且不需要再跑命令：
   [COMMAND] 留空或写 UNSUPPORTED；把 Markdown 分析只写在 [EXPLAIN]
9. 上下文里的旧输出只供参考，禁止原样或改写后当作 [COMMAND] 再下发

注意事项：
- 用户可能用中文或英文描述
- 上下文可能是之前命令的输出
- 用户可能只想查看系统信息，不需要执行任何命令（返回空）

危险操作检测：以下命令必须标记为危险且需要确认：
- rm -rf 任何路径
- 格式化磁盘、分区操作
- 系统关机/重启
- 修改系统关键文件（/etc/passwd, /etc/shadow, /etc/sudoers）
- 杀掉关键进程
- 远程下载并直接执行脚本（curl/wget | bash）

当检测到危险操作时，在返回的命令前加 "⚠️DANGEROUS:" 前缀。

输出格式（严格遵循）：
[EXPLAIN] 简短中文解释（1句话；若只需解读上下文可写多行 Markdown）
[COMMAND] 实际执行的命令（必须是可在 shell 里直接敲的一行/多行命令；禁止自然语言）
[WARN] 警告信息（如有）
[DANGEROUS] true/false

示例：
用户: 查看内存使用
[EXPLAIN] 查看内存使用情况
[COMMAND] free -h
[DANGEROUS] false

用户:（上下文已有 free/df 输出）资源是否紧张？
[EXPLAIN] **结论：内存偏紧，磁盘正常。**
- 可用内存偏低；根分区使用率正常
[COMMAND]
[DANGEROUS] false

（当运行环境为 Windows PowerShell 时，磁盘/内存示例应类似：）
用户: 查看磁盘空间
[EXPLAIN] 查看逻辑磁盘空间
[COMMAND] Get-PSDrive -PSProvider FileSystem | Format-Table Name,Used,Free
[DANGEROUS] false

用户: 删掉tmp目录
[EXPLAIN] 删除临时目录
[COMMAND] rm -rf /tmp/*
[WARN] 将删除 /tmp 目录下所有文件
[DANGEROUS] true

用户: 启动nginx
[EXPLAIN] 启动 nginx 服务
[COMMAND] systemctl start nginx
[DANGEROUS] false
"""

REFINE_PLAN_STEP_SYSTEM = """你是运维终端助手。用户正在执行「多步计划」中的某一步，希望对命令做修订后重试。

任务：根据「原计划命令」「用户补充要求」「终端上下文」生成**一条**新的可执行命令，替换本步（单行；不要多行脚本）。

硬性规则：
1. 严格遵守用户消息中的「运行环境」说明（Linux bash vs Windows PowerShell）。
2. [COMMAND] 后只能跟**一行**可执行内容，禁止换行、禁止 markdown 围栏。
3. 若无法合理满足补充要求，[COMMAND] 写 UNSUPPORTED（并 [EXPLAIN] 说明原因）。
4. 危险操作仍须 [DANGEROUS] true，且可在命令前加 ⚠️DANGEROUS: 前缀（与主助手一致）。

输出格式（严格遵循）：
[EXPLAIN] 一句话说明新命令如何体现补充要求
[COMMAND] 单行命令或 UNSUPPORTED
[WARN] 可选
[DANGEROUS] true/false
"""


# ─── systemctl 单元名解析（规则引擎；禁止空白「启?动?」误匹配整句）──────────────

_KNOWN_SVC_UNITS = frozenset(
    {"nginx", "docker", "httpd", "mysql", "postgres", "redis", "mongod"}
)


def _systemd_unit_from_match(m: re.Match, default: str = "nginx") -> str:
    """从「启动/停止/重启 … 服务」类正则的捕获组解析合法 systemd unit 名。"""
    g1 = (m.group(1) or "").strip()
    g2 = m.group(2)
    if g2 in _KNOWN_SVC_UNITS:
        return g2
    if g2 in ("service", "服务"):
        if g1 and re.fullmatch(r"[a-zA-Z0-9._@-]+", g1):
            return g1
        return default
    if g1 and re.fullmatch(r"[a-zA-Z0-9._@-]+", g1):
        return g1
    if g2 and g2 not in ("service", "服务"):
        return g2
    return default


# ─── 轻量规则引擎（无 LLM 时 fallback）──────────────────────────────────────
# 查看动词必须非空（禁止 查?看? 空匹配导致「内存…」误绑）
_NL_VIEW = (
    r"(?:查看|查一下|看看|看下|查下|查查|看一看|瞅瞅|"
    r"show|display|check|查|看)"
)
_NL_HELP = r"(?:请|麻烦|帮我|帮忙)?"

SIMPLE_PATTERNS_UNIX = [
    # (pattern, replacement_or_callable, explanation, is_dangerous)
    # 资源类窄问（内存/磁盘/进程排行等）改由 nl_readonly_intent 严格匹配，不在此表用宽正则。
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(网络|net|网卡|流量)", "ss -tunlp | head -20", "查看网络连接", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(端口|port)", "ss -tunlp | grep LISTEN", "查看监听端口", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(系统|system|版本|os)", "uname -a && cat /etc/os-release | head -5", "查看系统信息", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(环境变量|env)", "env | sort", "查看环境变量", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(定时任务|cron)", "crontab -l && cat /etc/crontab", "查看定时任务", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(日志|log)\s*(.*)", lambda m: f"tail -50 /var/log/{m.group(2) or 'syslog'}", "查看日志", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(服务|service)\s*(.*)", lambda m: f"systemctl status {m.group(2) or ''}", "查看服务状态", False),
    # 必须显式出现「启动/启 动/开启/打开」，禁止 ^启?动? 在句首空匹配后靠句中 nginx 误触达
    (
        r"^(?:帮我?)?(?:启动|启\s*动|开启|打开)\s*(.*?)(服务|service|nginx|docker|httpd|mysql|postgres|redis|mongod)",
        lambda m: f"systemctl start {_systemd_unit_from_match(m)}",
        "启动服务",
        False,
    ),
    (
        r"^(?:帮我?)?(?:停止|停\s*止|关停)\s*(.*?)(服务|service|nginx|docker|httpd|mysql|postgres|redis|mongod)",
        lambda m: f"systemctl stop {_systemd_unit_from_match(m)}",
        "停止服务（危险）",
        True,
    ),
    (
        r"^(?:帮我?)?(?:重启|重\s*启|重新启动)\s*(.*?)(服务|service|nginx|docker|httpd|mysql|postgres|redis|mongod)",
        lambda m: f"systemctl restart {_systemd_unit_from_match(m)}",
        "重启服务",
        False,
    ),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(IP|ip地址|网卡)", "ip addr && hostname -I", "查看 IP 地址", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(用户|user)", "cat /etc/passwd | tail -10", "查看用户列表", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(防火墙|firewall|iptables)", "iptables -L -n && systemctl status firewalld", "查看防火墙规则", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(docker|容器)", "docker ps -a", "查看 Docker 容器", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(nginx)", "nginx -v && nginx -t && ps aux | grep nginx", "查看 nginx 状态", False),
    (r"^监控(.*)\s*(10|20|30|60|120)秒", lambda m: f"watch -n {m.group(2) or 5} '{m.group(1) or 'uptime'}'", "监控命令", False),
    (r"^(?:帮我?)?清理(.*)缓存", lambda m: f"sync && echo 3 > /proc/sys/vm/drop_caches", "清理内存缓存（危险）", True),
    (r"^删[除]?.*日志", "find /var/log -name '*.log' -type f -mtime +7 -delete 2>/dev/null; echo '已清理7天前日志'", "清理旧日志", False),
    (r"^磁盘使用情况", "du -sh /* 2>/dev/null | sort -hr | head -20", "查看目录磁盘占用", False),
    (r"^连接数", "ss -s", "查看网络连接统计", False),
    (r"^重启.*(nginx|apache|httpd)", "systemctl restart nginx", "重启 nginx", False),
    (r"^重启.*(docker|containerd)", "systemctl restart docker", "重启 Docker", False),
    (r"^(?:帮我?)?测试.*(连通|ping|网络)", lambda m: "ping -c 4 8.8.8.8", "测试网络连通性", False),
    (r"^DNS.*解析", "nslookup google.com 2>/dev/null || dig google.com", "测试 DNS 解析", False),
    (r"^curl.*", None, "执行 curl 请求", False),
    (r"^wget.*", None, "下载文件", False),
    (r"^git.*", None, "执行 git 操作", False),
    (r"^ssh.*", None, "SSH 连接", False),
    (r"^scp.*", None, "远程复制文件", False),
    (r"^tar.*", None, "归档/解压文件", False),
    (r"^grep.*", None, "搜索文本", False),
    (r"^awk.*", None, "文本处理", False),
    (r"^sed.*", None, "文本替换", False),
    (r"^find.*", None, "查找文件", False),
    (r"^ls\s+-la", "ls -la", "列出文件详情", False),
    (r"^ls\s+", None, "列出目录内容", False),
    (r"^pwd", "pwd", "显示当前目录", False),
    (r"^whoami", "whoami", "显示当前用户", False),
    (r"^hostname", "hostname", "显示主机名", False),
    (r"^uptime", "uptime", "显示运行时间", False),
    (r"^df\s+-h", "df -h", "查看磁盘", False),
    (r"^free\s+-h", "free -h", "查看内存", False),
    (r"^top\s+", "top -bn1 | head -20", "查看进程", False),
    (r"^ps\s+", "ps aux | head -20", "查看进程", False),
    (r"^cat\s+", None, "查看文件内容", False),
    (r"^head\s+", None, "查看文件头部", False),
    (r"^tail\s+", None, "查看文件尾部", False),
    (r"^clear|cls", "clear", "清屏", False),
    (r"^exit|quit", "exit", "退出终端", False),
    (r"^help|\?", "echo '输入自然语言描述运维操作，或直接输入shell命令'", "显示帮助", False),
]

SIMPLE_PATTERNS_WINDOWS = [
    # 资源类窄问改由 nl_readonly_intent；此处仅保留显式动词的其它查询
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(网络|net|网卡|流量)",
     "netstat -ano | Select-Object -First 40",
     "查看网络连接", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(端口|port)",
     "netstat -ano | findstr LISTEN",
     "查看监听端口", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(系统|system|版本|os)",
     "systeminfo",
     "查看系统信息", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(环境变量|env)",
     "Get-ChildItem Env: | Sort-Object Name",
     "查看环境变量", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(定时任务|cron)",
     "Get-ScheduledTask | Select-Object TaskName,State -First 25",
     "查看计划任务", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(日志|log)\s*(.*)",
     lambda m: "Get-EventLog -LogName Application -Newest 50",
     "查看日志", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(服务|service)\s*(.*)",
     lambda m: f"Get-Service -Name '{(m.group(2) or '*').strip() or '*'}' | Format-Table Status,Name,DisplayName",
     "查看服务状态", False),
    (
        r"^(?:帮我?)?(?:启动|启\s*动|开启|打开)\s*(.*?)(服务|service|nginx|docker|httpd|mysql|postgres|redis|mongod)",
        lambda m: f"Start-Service '{_systemd_unit_from_match(m)}'",
        "启动服务",
        False,
    ),
    (
        r"^(?:帮我?)?(?:停止|停\s*止|关停)\s*(.*?)(服务|service|nginx|docker|httpd|mysql|postgres|redis|mongod)",
        lambda m: f"Stop-Service '{_systemd_unit_from_match(m)}' -Force",
        "停止服务（危险）",
        True,
    ),
    (
        r"^(?:帮我?)?(?:重启|重\s*启|重新启动)\s*(.*?)(服务|service|nginx|docker|httpd|mysql|postgres|redis|mongod)",
        lambda m: f"Restart-Service '{_systemd_unit_from_match(m)}'",
        "重启服务",
        False,
    ),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(IP|ip地址|网卡)",
     "Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias,IPAddress,PrefixLength",
     "查看 IP 地址", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(用户|user)",
     "Get-LocalUser | Format-Table Name,Enabled,LastLogon",
     "查看本地用户", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(防火墙|firewall|iptables)",
     "netsh advfirewall show allprofiles",
     "查看防火墙概要", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(docker|容器)",
     "docker ps -a",
     "查看 Docker 容器", False),
    (rf"^{_NL_HELP}{_NL_VIEW}\s*(nginx)",
     "Get-Process nginx -ErrorAction SilentlyContinue; if (Get-Command nginx -ErrorAction SilentlyContinue) { nginx -v }",
     "查看 nginx 相关进程", False),
    (r"^监控(.*)\s*(10|20|30|60|120)秒",
     lambda m: (
         f"while ($true) {{ Clear-Host; Get-Date; "
         f"Get-Process | Sort-Object CPU -Descending | Select-Object -First 8; "
         f"Start-Sleep -Seconds {m.group(2) or 5} }}"
     ),
     "监控命令", False),
    (r"(优化|清理|释放).*(内存|临时|TEMP|缓存|临时文件)",
     (
         "$before = Get-CimInstance Win32_OperatingSystem; "
         "Get-ChildItem -Path $env:TEMP -Force -ErrorAction SilentlyContinue | "
         "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue; "
         "Get-ChildItem -Path $env:WINDIR\\Temp -Force -ErrorAction SilentlyContinue | "
         "Remove-Item -Force -Recurse -ErrorAction SilentlyContinue; "
         "Clear-DnsClientCache -ErrorAction SilentlyContinue; "
         "$after = Get-CimInstance Win32_OperatingSystem; "
         "[PSCustomObject]@{"
         "FreeGBBefore=[math]::Round($before.FreePhysicalMemory/1MB,2); "
         "FreeGBAfter=[math]::Round($after.FreePhysicalMemory/1MB,2); "
         "Top=[string]::Join(',', ((Get-Process | Sort-Object WorkingSet64 -Descending | "
         "Select-Object -First 5).ProcessName))"
         "} | ConvertTo-Json -Compress"
     ),
     "清理临时文件并回报内存", False),
    (r"^(?:帮我?)?清理\s*缓存$",
     "Write-Warning 'Windows 不建议手动清理物理内存页；请关闭占用内存的程序或重启。'",
     "清理内存缓存（提示）", False),
    (r"^删[除]?.*日志",
     "Get-ChildItem $env:TEMP -Filter *.log -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue; Write-Host '已尝试清理用户 Temp 下日志'",
     "清理日志（用户 Temp）", False),
    (r"^磁盘使用情况",
     "Get-Volume | Where-Object DriveLetter | Format-Table DriveLetter,FileSystemLabel,Size,SizeRemaining",
     "查看卷空间", False),
    (r"^连接数",
     "netstat -ano | Measure-Object -Line",
     "查看网络连接行数", False),
    (r"^重启.*(nginx|apache|httpd)",
     "Restart-Service nginx -ErrorAction SilentlyContinue",
     "重启 nginx", False),
    (r"^重启.*(docker|containerd)",
     "Restart-Service docker -ErrorAction SilentlyContinue",
     "重启 Docker", False),
    (r"^(?:帮我?)?测试.*(连通|ping|网络)",
     lambda m: "Test-Connection -ComputerName 8.8.8.8 -Count 4",
     "测试网络连通性", False),
    (r"^DNS.*解析",
     "Resolve-DnsName google.com -ErrorAction SilentlyContinue",
     "测试 DNS 解析", False),
    (r"^curl.*", None, "执行 curl 请求", False),
    (r"^wget.*", None, "下载文件", False),
    (r"^git.*", None, "执行 git 操作", False),
    (r"^ssh.*", None, "SSH 连接", False),
    (r"^scp.*", None, "远程复制文件", False),
    (r"^tar.*", None, "归档/解压文件", False),
    (r"^grep.*", None, "搜索文本", False),
    (r"^awk.*", None, "文本处理", False),
    (r"^sed.*", None, "文本替换", False),
    (r"^find.*", None, "查找文件", False),
    (r"^ls\s+-la", "Get-ChildItem -Force", "列出文件详情", False),
    (r"^ls\s+", None, "列出目录内容", False),
    (r"^pwd", "Get-Location", "显示当前目录", False),
    (r"^whoami", "whoami", "显示当前用户", False),
    (r"^hostname", "hostname", "显示主机名", False),
    (r"^uptime",
     "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime",
     "上次启动时间", False),
    (r"^df\s+-h",
     "Get-PSDrive -PSProvider FileSystem | Format-Table Name,Used,Free",
     "查看磁盘", False),
    (r"^free\s+-h",
     "Get-CimInstance Win32_OperatingSystem | Format-List TotalVisibleMemorySize,FreePhysicalMemory",
     "查看内存", False),
    (r"^top\s+",
     "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20",
     "查看进程", False),
    (r"^ps\s+",
     "Get-Process | Sort-Object WS -Descending | Select-Object -First 20",
     "查看进程", False),
    (r"^cat\s+", None, "查看文件内容", False),
    (r"^head\s+", None, "查看文件头部", False),
    (r"^tail\s+", None, "查看文件尾部", False),
    (r"^clear|cls", "cls", "清屏", False),
    (r"^exit|quit", "exit", "退出终端", False),
    (r"^help|\?",
     "Write-Host '输入自然语言描述运维操作，或直接输入命令'",
     "显示帮助", False),
]



# ─── LLM Shell 主类 ─────────────────────────────────────────────────────────

class LLMPromptProcessor:
    """自然语言 → Shell 命令转换。优先使用 LLM，fallback 到规则引擎。"""

    def __init__(self):
        self._llm = None
        self._llm_available = False
        self._init_llm()

    def _init_llm(self):
        try:
            self._llm = get_llm()
            self._llm_available = self._llm.is_available
            if self._llm_available:
                logger.info(f"LLM 增强已启用（Provider: {self._llm.active_name}）")
            else:
                logger.info("无 LLM API Key，将使用规则引擎")
        except Exception as e:
            logger.warning(f"LLM 初始化失败: {e}")
            self._llm_available = False

    def refresh_llm(self) -> None:
        """丢弃全局 LLM 单例并重新拉取配置（热更新）。"""
        reset_llm_singleton()
        self._init_llm()

    def refine_plan_step_command(
        self,
        *,
        plan_explanation: str,
        step_title: str,
        prior_command: str,
        user_note: str,
        session_context: str = "",
        runtime_hint: str = "",
        shell_profile: str = ShellProfile.UNIX.value,
        prior_steps_summary: str = "",
        ui_locale: str = "zh-CN",
    ) -> PromptResult:
        """
        计划「重试本步」且用户填写补充要求时：合并上下文调用 LLM，产出单条替代命令。
        LLM 不可用时返回 should_execute=False。
        """
        _ = shell_profile  # 与主流程一致，提示词内已由 runtime_hint 约束环境
        note = (user_note or "").strip()
        if not note:
            return PromptResult(should_execute=False, explanation="无补充要求")
        if not self._llm_available:
            return PromptResult(should_execute=False, explanation="LLM 未配置")

        hint_block = (runtime_hint + "\n\n") if (runtime_hint or "").strip() else ""
        ctx = (session_context or "")[-4000:]
        prev_blk = ""
        ps = (prior_steps_summary or "").strip()
        if ps:
            prev_blk = f"本计划已完成步骤摘要（勿重复执行，仅供上下文）：\n{ps[:2000]}\n\n"
        user_blob = (
            f"{hint_block}"
            "【计划单步重试 — 请生成替代命令】\n\n"
            f"{prev_blk}"
            f"计划整体说明：\n{(plan_explanation or '')[:1600]}\n\n"
            f"本步标题：{(step_title or '').strip()}\n"
            f"本步当前命令：\n{(prior_command or '').strip()}\n\n"
            f"用户补充要求（必须合并进新命令的意图中）：\n{note}\n\n"
            f"终端最近输出（仅供参考）：\n{ctx or '（无）'}\n"
        )
        from chibyterm.ui_locale import ai_language_instruction

        messages = [
            {
                "role": "system",
                "content": REFINE_PLAN_STEP_SYSTEM + ai_language_instruction(ui_locale),
            },
            {"role": "user", "content": user_blob},
        ]
        eff = get_effective_llm_settings()
        ctx_budget = min(32000, max(512, int(eff.get("max_tokens") or 4096) * 6))
        messages = truncate_chat_messages(messages, ctx_budget)
        no_think = _resolve_no_think_override(eff, None)
        try:
            response = self._llm.chat(
                messages,
                temperature=0.15,
                max_tokens=min(4096, int(eff.get("max_tokens") or 2048)),
                no_think=no_think,
            )
            if not response:
                return PromptResult(should_execute=False, explanation="LLM 返回为空")
            return self._parse_llm_response(response)
        except Exception as e:  # pragma: no cover
            logger.warning("refine_plan_step_command LLM 失败: %s", e)
            hint = _llm_timeout_hint(e)
            return PromptResult(
                should_execute=False,
                explanation=f"LLM 调用失败: {e}" + (hint or ""),
            )

    def _is_dangerous(self, command: str) -> tuple:
        """检测危险命令。返回 (是否危险, 警告信息)。"""
        for pattern in DANGEROUS_PATTERNS_COMPILED:
            if pattern.search(command):
                # 给出具体警告
                return True, f"⚠️ 检测到危险操作: {command.strip()[:60]}"
        return False, ""

    def _parse_llm_response(self, text: str) -> PromptResult:
        """解析 LLM 返回的结构化结果。"""
        result = PromptResult()

        lines = text.strip().split("\n")
        current_section = None
        command_lines = []

        for line in lines:
            line = line.strip()
            upper = line.upper()

            if upper.startswith("[EXPLAIN]"):
                result.explanation = line.split("]", 1)[1].strip()
                current_section = "explain"
            elif upper.startswith("[COMMAND]"):
                current_section = "command"
                cmd = line.split("]", 1)[1].strip()
                if cmd and looks_like_unsupported_command(cmd):
                    reason = _unsupported_reason_from_command(cmd)
                    if reason:
                        expl = (result.explanation or "").strip()
                        if not expl:
                            result.explanation = reason
                        elif reason not in expl:
                            result.explanation = f"{expl}\n\n{reason}".strip()
                    current_section = "unsupported"
                elif cmd:
                    command_lines.append(cmd)
            elif upper.startswith("[WARN]"):
                result.warning = line.split("]", 1)[1].strip()
                current_section = "warn"
            elif upper.startswith("[DANGEROUS]"):
                result.is_dangerous = "true" in line.lower().split("]", 1)[1].lower()
                current_section = "dangerous"
            elif current_section == "command" and line and not line.startswith("["):
                # 多行命令
                if looks_like_unsupported_command(line):
                    reason = _unsupported_reason_from_command(line)
                    if reason:
                        expl = (result.explanation or "").strip()
                        if not expl:
                            result.explanation = reason
                        elif reason not in expl:
                            result.explanation = f"{expl}\n\n{reason}".strip()
                    current_section = "unsupported"
                else:
                    command_lines.append(line)
            elif current_section == "explain" and line and not line.startswith("["):
                prev = (result.explanation or "").rstrip()
                result.explanation = f"{prev}\n{line}".strip() if prev else line

        if command_lines:
            result.command = "\n".join(command_lines)
            result.should_execute = True

        # 二次检查危险命令
        if result.command:
            dangerous, warn = self._is_dangerous(result.command)
            if dangerous and not result.is_dangerous:
                result.is_dangerous = True
            if dangerous and not result.warning:
                result.warning = warn

        # 需要确认的危险操作
        if result.is_dangerous:
            result.confirm_required = True

        return apply_prompt_result_risk(sanitize_prompt_result_command(result))

    def _match_simple_pattern(
        self, user_input: str, shell_profile: str = ShellProfile.UNIX.value
    ) -> Optional[PromptResult]:
        """规则引擎 fallback：先严格资源意图，再表驱动模式。"""
        from chibyterm.nl_readonly_intent import classify_readonly_intent, normalize_nl_query

        q = normalize_nl_query(user_input)
        ct = (
            "winrm"
            if shell_profile == ShellProfile.POWERSHELL.value
            else "ssh"
        )
        hit = classify_readonly_intent(q, conn_type=ct)
        if hit is not None:
            result = PromptResult()
            result.explanation = hit.label
            result.command = hit.command
            result.should_execute = True
            result.is_dangerous = False
            result.confirm_required = False
            return apply_prompt_result_risk(result)

        table = (
            SIMPLE_PATTERNS_WINDOWS
            if shell_profile == ShellProfile.POWERSHELL.value
            else SIMPLE_PATTERNS_UNIX
        )
        for pattern, command, explanation, is_dangerous in table:
            m = re.match(pattern, q.strip(), re.IGNORECASE)
            if m:
                result = PromptResult()
                result.explanation = explanation
                result.should_execute = True
                result.is_dangerous = bool(is_dangerous)
                result.confirm_required = bool(is_dangerous)
                result.warning = "⚠️ 危险操作" if is_dangerous else ""

                if callable(command):
                    try:
                        result.command = command(m)
                    except Exception:
                        result.command = q.strip()
                        result.explanation = "执行原始输入"
                elif command is not None:
                    result.command = command
                else:
                    # 直接透传原命令
                    result.command = q.strip()
                    result.explanation = explanation

                return apply_prompt_result_risk(result)
        return None

    def process(
        self,
        user_input: str,
        session_context: str = "",
        runtime_hint: str = "",
        shell_profile: str = ShellProfile.UNIX.value,
        llm_params: Optional[Dict[str, Any]] = None,
        ui_locale: str = "zh-CN",
    ) -> PromptResult:
        """
        将自然语言转换为可执行的 shell 命令。
        已配置 LLM（API Key）时优先调用大模型；仅当 LLM 无有效输出、调用失败或
        未配置 Key 时，才使用轻量规则兜底；看起来像裸 shell 命令的输入仍直接透传。
        """
        from chibyterm.ui_locale import normalize_ui_locale

        ui_locale = normalize_ui_locale(ui_locale)
        user_input = user_input.strip()
        if not user_input:
            return PromptResult(should_execute=False)

        # Step 1: 如果看起来直接就是 shell 命令，直接透传
        if self._looks_like_shell_command(user_input):
            result = PromptResult()
            result.command = user_input
            if ui_locale == "en":
                result.explanation = "Execute shell command as typed"
            elif ui_locale == "zh-TW":
                result.explanation = "直接執行 shell 命令"
            else:
                result.explanation = "直接执行 shell 命令"
            result.should_execute = True
            return apply_prompt_result_risk(result)

        # Step 2: 已配置 LLM 时优先大模型，规则在 _process_with_llm 内兜底
        if self._llm_available:
            return self._process_with_llm(
                user_input,
                session_context,
                runtime_hint,
                shell_profile,
                llm_params,
                ui_locale=ui_locale,
            )

        # Step 3: 无 LLM 时仅用规则引擎
        simple = self._match_simple_pattern(user_input, shell_profile)
        if simple:
            return simple

        # Step 4: 无法识别 — 说明是规则未命中，而非笼统「听不懂」
        result = PromptResult()
        result.should_execute = False
        if self._llm_available:
            result.explanation = (
                "规则未命中该问法，且大模型未给出可执行命令。"
                "请换一种更具体的说法，例如：「内存还剩多少」「系统有几个用户」。"
            )
        else:
            result.explanation = (
                "未接入大模型，且规则未覆盖该问法。"
                "请换已支持的说法（如「内存还剩多少」「系统有几个用户」），"
                "或配置 LLM API Key / 切换智能型。"
            )
        return result

    def _process_with_llm(
        self,
        user_input: str,
        session_context: str,
        runtime_hint: str,
        shell_profile: str,
        llm_params: Optional[Dict[str, Any]] = None,
        ui_locale: str = "zh-CN",
    ) -> PromptResult:
        """使用 LLM 处理。"""
        from chibyterm.ui_locale import ai_language_instruction, normalize_ui_locale

        ui_locale = normalize_ui_locale(ui_locale)
        temperature, max_tokens = _chat_params_from_settings(llm_params)
        eff = get_effective_llm_settings()
        ctx_budget = min(32000, max(512, int(eff.get("max_tokens") or max_tokens) * 6))
        context_hint = ""
        if session_context:
            if ui_locale == "en":
                context_hint = f"\n\nCurrent terminal context (recent output):\n{session_context[-1000:]}"
            elif ui_locale == "zh-TW":
                context_hint = f"\n\n目前終端上下文（最近輸出）：\n{session_context[-1000:]}"
            else:
                context_hint = f"\n\n当前终端上下文（最近输出）：\n{session_context[-1000:]}"
        hint_block = ""
        if runtime_hint:
            hint_block = f"{runtime_hint}\n\n"
        if ui_locale == "en":
            user_prefix = "User input: "
        elif ui_locale == "zh-TW":
            user_prefix = "使用者輸入："
        else:
            user_prefix = "用户输入："

        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + ai_language_instruction(ui_locale),
            },
            {
                "role": "user",
                "content": f"{hint_block}{user_prefix}{user_input}{context_hint}",
            },
        ]
        messages = truncate_chat_messages(messages, ctx_budget)
        no_think = _resolve_no_think_override(eff, llm_params)

        try:
            response = self._llm.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                no_think=no_think,
            )
            if not response:
                return self._match_simple_pattern(user_input, shell_profile) or PromptResult(
                    should_execute=False,
                    explanation="LLM 返回为空",
                )

            result = self._parse_llm_response(response)

            if result.command and result.command.strip():
                return result

            # LLM 未给出可执行命令 → 规则兜底（短句快捷指令仍可用）
            fallback = self._match_simple_pattern(user_input, shell_profile)
            if fallback:
                return fallback

            result.should_execute = False
            result.explanation = result.explanation or f"无法将「{user_input}」转换为可执行命令"
            return result
        except Exception as e:
            logger.error(f"LLM 处理失败: {e}")
            hint = _llm_timeout_hint(e)
            return self._match_simple_pattern(user_input, shell_profile) or PromptResult(
                should_execute=False,
                explanation=f"LLM 调用失败: {e}" + (hint or ""),
            )

    @staticmethod
    def _looks_like_shell_command(text: str) -> bool:
        """简单判断输入是否看起来像 shell 命令。"""
        shell_keywords = [
            "rm", "cp", "mv", "mkdir", "chmod", "chown", "cat", "ls", "grep",
            "awk", "sed", "find", "tar", "gzip", "ssh", "scp", "curl", "wget",
            "docker", "kubectl", "systemctl", "service", "ps", "kill", "top",
            "df", "du", "free", "mount", "umount", "ping", "netstat", "ss",
            "iptables", "ip", "ifconfig", "route", "nslookup", "dig", "host",
            "git", "svn", "make", "cmake", "gcc", "python", "python3", "node",
            "npm", "pip", "apt", "yum", "dnf", "pacman", "yum", "dnf",
            "journalctl", "tail", "head", "less", "more", "cut", "sort",
            "uniq", "wc", "xargs", "tee", "diff", "patch", "export",
            "cd", "pwd", "echo", "printf", "source", "alias", "unalias",
            "clear", "exit", "history", "man", "which", "whereis",
            "nc", "telnet", "openssl", "gzip", "gunzip", "zip", "unzip",
            "hostname", "uptime", "whoami", "id", "users", "w",
        ]
        first_word = text.split()[0] if text.split() else ""
        return first_word in shell_keywords
