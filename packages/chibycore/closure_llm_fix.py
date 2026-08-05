"""闭环失败时：调用全局 LLM 生成修复命令列表（JSON），供 retry runner 使用。"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from chibycore.closure_service import ClosurePayload
from chibycore.llm_providers import get_llm, strip_model_thinking_output

logger = logging.getLogger(__name__)

FIX_SYSTEM_PROMPT_BASE = """你是运维命令修复助手。用户上一条命令执行失败，请根据 stdout/stderr/exit_code 给出**可执行的修复命令**。

硬性要求：
1. 只输出一段 **合法 JSON 对象**，不要 markdown 代码围栏，不要其它解释文字。
2. JSON 格式严格为：{"commands":["命令1","命令2"]}，最多 3 条字符串；每条为单行命令，不要换行符。
3. 命令应比原命令更安全、可逆优先；不要建议 rm -rf / 等毁灭性操作。
4. 若无法给出合理修复，返回 {"commands":[]}。
5. **必须严格遵守 shell_profile**：跨壳命令一律禁止（见下文环境铁律）。
6. **优先覆盖用户原意图**：若失败原因是权限不足，请给出能完成**完整原任务**的提权命令（例如对整条复合命令使用 `sudo bash -lc '…'`），不要只把原命令的第一段（如单独的 `nginx -t`）提权后当作任务完成。
7. 若只需先装依赖/改环境，第一条可为前置修复，但应尽量在同一轮候选中给出「前置 + 续跑原任务」的完整命令。
"""

# PowerShell / cmdlet 指纹（用于 unix 目标上硬过滤）
_POWERSHELL_CMDLET_RE = re.compile(
    r"(?i)\b("
    r"Test-Path|Remove-Item|Get-Item|Get-ChildItem|Get-Content|Set-Content|"
    r"Get-Process|Stop-Process|Get-Service|Stop-Service|Restart-Service|"
    r"Get-PSDrive|Get-CimInstance|Get-WmiObject|Select-Object|Where-Object|"
    r"ForEach-Object|Write-Output|Write-Host|Clear-Item|Clear-RecycleBin|"
    r"New-Item|Copy-Item|Move-Item|Rename-Item|Out-String|ConvertTo-Json|"
    r"\$env:|Invoke-Expression|\$_\."
    r")\b"
)


def _normalize_shell_profile(shell_profile: str) -> str:
    sp = (shell_profile or "unix").strip().lower()
    if sp in ("powershell", "pwsh", "windows", "winrm"):
        return "powershell"
    return "unix"


def _distro_env_clause(
    distro_family: Optional[str],
    pkg_manager: Optional[str],
) -> str:
    fam = normalize_distro_family(distro_family)
    if not fam:
        return ""
    pkg = (pkg_manager or "").strip().lower() or {
        "debian": "apt",
        "rhel": "dnf",
        "alpine": "apk",
        "suse": "zypper",
        "arch": "pacman",
    }.get(fam, "unknown")
    forbid = {
        "debian": "yum/dnf/apk/zypper/pacman",
        "rhel": "apt/apk/zypper/pacman",
        "alpine": "apt/yum/dnf/zypper/pacman",
        "suse": "apt/yum/dnf/apk/pacman",
        "arch": "apt/yum/dnf/apk/zypper",
    }.get(fam, "")
    return (
        f"发行版铁律：distro_family={fam}，装包须用 {pkg}；"
        f"禁止用 {forbid} 做 install/remove。"
    )


def fix_system_prompt_for_profile(
    shell_profile: str,
    *,
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
) -> str:
    sp = _normalize_shell_profile(shell_profile)
    if sp == "powershell":
        env = (
            "环境铁律：目标是 **Windows PowerShell**。"
            "命令必须是 PowerShell cmdlet/语法；禁止把 bash 的 `[ -f ]`、`sudo`、`apt` 等当主方案。"
        )
    else:
        env = (
            "环境铁律：目标是 **Linux/Unix bash（或 sh）**。"
            "禁止任何 PowerShell/cmdlet（Test-Path、Remove-Item、Get-*、Write-Output、`.\\path`、`$_` 等）。"
            "文件删除示例：`rm -f path` 或 "
            "`if [ -e path ]; then rm -f path; else echo missing; fi`。"
            "若失败原因已是「文件不存在」，可返回 "
            '`{"commands":["test ! -e path && echo already_absent"]}` 或空数组，切勿改用 PowerShell。'
        )
        distro = _distro_env_clause(distro_family, pkg_manager)
        if distro:
            env = env + "\n" + distro
    return FIX_SYSTEM_PROMPT_BASE + "\n" + env


def looks_like_powershell_command(cmd: str) -> bool:
    s = (cmd or "").strip()
    if not s:
        return False
    if _POWERSHELL_CMDLET_RE.search(s):
        return True
    # `if (Test-Path ...)` / `Remove-Item -Path` 等
    if re.search(r"(?i)\bif\s*\(\s*Test-Path\b", s):
        return True
    if re.search(r"(?i)\.[\\/][^\s]+.*-(Path|Force|Recurse)\b", s) and "-" in s:
        # 弱信号：带 -Path/-Force 的 Windows 风格，且含 .\
        if re.search(r"(?i)-(Path|LiteralPath|Force|Recurse)\b", s):
            return True
    return False


# 明显的装包/卸包命令指纹 → 所属包管理器（仅硬丢弃跨族装包，避免误杀脚本字符串）
_PKG_ACTION_RES = {
    "apt": re.compile(r"(?i)\bapt(?:-get)?\s+(?:install|remove|purge|autoremove)\b"),
    "yum": re.compile(r"(?i)\byum\s+(?:install|remove|erase)\b"),
    "dnf": re.compile(r"(?i)\bdnf\s+(?:install|remove|erase)\b"),
    "apk": re.compile(r"(?i)\bapk\s+(?:add|del|delete)\b"),
    "zypper": re.compile(r"(?i)\bzypper\s+(?:install|in|remove|rm)\b"),
    "pacman": re.compile(r"(?i)\bpacman\s+-[SRsr]+\b"),
}

_FAMILY_ALLOWED_PKG: Dict[str, frozenset] = {
    "debian": frozenset({"apt"}),
    "rhel": frozenset({"dnf", "yum"}),
    "alpine": frozenset({"apk"}),
    "suse": frozenset({"zypper"}),
    "arch": frozenset({"pacman"}),
}


def normalize_distro_family(distro_family: Optional[str]) -> str:
    f = (distro_family or "").strip().lower()
    if f in _FAMILY_ALLOWED_PKG:
        return f
    return ""


def looks_like_cross_family_pkg_command(
    cmd: str,
    distro_family: Optional[str],
) -> bool:
    """unix + 已知 family 时，是否为明显跨族装包/卸包命令。"""
    fam = normalize_distro_family(distro_family)
    if not fam:
        return False
    s = (cmd or "").strip()
    if not s:
        return False
    allowed = _FAMILY_ALLOWED_PKG[fam]
    for mgr, rx in _PKG_ACTION_RES.items():
        if mgr in allowed:
            continue
        if rx.search(s):
            return True
    return False


def filter_fix_commands_for_shell(
    commands: List[str],
    shell_profile: str,
    *,
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,  # noqa: ARG001 — 预留；硬过滤以 family 为准
) -> List[str]:
    """丢掉与目标壳/发行版命令族明显不符的修复命令。"""
    _ = pkg_manager
    sp = _normalize_shell_profile(shell_profile)
    fam = normalize_distro_family(distro_family) if sp == "unix" else ""
    out: List[str] = []
    for c in commands or []:
        line = (c or "").strip()
        if not line:
            continue
        if sp == "unix" and looks_like_powershell_command(line):
            logger.warning(
                "closure_fix: 丢弃 PowerShell 候选（目标 unix）: %s",
                line[:120],
            )
            continue
        if fam and looks_like_cross_family_pkg_command(line, fam):
            logger.warning(
                "closure_fix: 丢弃跨族装包候选（family=%s）: %s",
                fam,
                line[:120],
            )
            continue
        out.append(line)
    return out


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    s = (text or "").strip()
    if not s:
        return None
    s = strip_model_thinking_output(s)
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            obj = json.loads(s[i : j + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None


def parse_fix_commands_json(text: str) -> List[str]:
    obj = _extract_json_object(text)
    if not obj:
        return []
    raw = obj.get("commands")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for x in raw[:3]:
        if isinstance(x, str) and x.strip():
            line = x.strip().split("\n")[0].strip()
            if line:
                out.append(line)
    return out


def build_fix_user_message(
    history: List[ClosurePayload],
    shell_profile: str,
    *,
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
) -> str:
    sp = _normalize_shell_profile(shell_profile)
    fam = normalize_distro_family(distro_family) if sp == "unix" else ""
    pkg = (pkg_manager or "").strip().lower() if fam else ""
    blocks = []
    for i, cp in enumerate(history[-3:]):
        blocks.append(
            {
                "index": i,
                "effective_command": (cp.effective_command or "")[:800],
                "exit_code": cp.exit_code,
                "stdout_tail": (cp.stdout or "")[-2000:],
                "stderr_tail": (cp.stderr or "")[-2000:],
                "transport": cp.transport,
                "risk_level": getattr(cp.risk_level, "value", str(cp.risk_level)),
            }
        )
    if sp == "powershell":
        instruction = (
            "目标 shell_profile=powershell：只输出 PowerShell；"
            "输出仅含 JSON 的 commands 数组。"
        )
    else:
        instruction = (
            "目标 shell_profile=unix（Linux bash）：只输出 bash/sh；"
            "严禁 Test-Path / Remove-Item 等 PowerShell；"
            "输出仅含 JSON 的 commands 数组。"
        )
        if fam:
            instruction += (
                f" distro_family={fam}"
                + (f" pkg_manager={pkg}" if pkg else "")
                + "：装包必须用该族包管理器，禁止跨族 apt/yum/dnf/apk。"
            )
    payload: Dict[str, Any] = {
        "shell_profile": sp,
        "failed_attempts": blocks,
        "instruction": instruction,
    }
    if history:
        head = history[0]
        raw = (getattr(head, "raw_command", None) or getattr(head, "effective_command", None) or "")
        if raw:
            payload["original_command"] = str(raw)[:1200]
        hint = getattr(head, "nl_intent_hint", None) or ""
        if hint:
            payload["nl_intent_hint"] = str(hint)[:400]
    if fam:
        payload["distro_family"] = fam
        if pkg:
            payload["pkg_manager"] = pkg
    return json.dumps(payload, ensure_ascii=False)


def call_fix_pipeline_with_source(
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
) -> tuple[List[str], str]:
    """返回 (修复命令列表, 来源标签)。

    来源：``remediator`` | ``llm`` | ``heuristic`` | ``none``
    """
    sp = _normalize_shell_profile(shell_profile)
    fam = normalize_distro_family(distro_family) if sp == "unix" else ""
    pkg = (pkg_manager or "").strip().lower() if fam else ""

    def _filt(cmds: List[str]) -> List[str]:
        return filter_fix_commands_for_shell(
            cmds, sp, distro_family=fam or None, pkg_manager=pkg or None
        )

    try:
        from chibycore.remediator_fix_bridge import (
            call_remediator_for_fix_commands,
            remediator_fix_enabled,
        )

        if remediator_fix_enabled():
            fixes = _filt(
                call_remediator_for_fix_commands(history, shell_profile=sp),
            )
            if fixes:
                logger.info(
                    "closure_fix: remediator 产出 %d 条候选（已按 %s/%s 过滤）",
                    len(fixes),
                    sp,
                    fam or "-",
                )
                return fixes, "remediator"
            logger.info(
                "closure_fix: remediator 已启用但未产出可用候选（或已过滤），回退 LLM",
            )
    except Exception as ex:  # pragma: no cover
        logger.info("closure_fix: remediator 路径不可用，回退: %s", ex)
    fixes = _filt(
        call_llm_for_fix_commands(
            history,
            shell_profile=sp,
            distro_family=fam or None,
            pkg_manager=pkg or None,
        ),
    )
    if fixes:
        logger.info(
            "closure_fix: LLM 直修产出 %d 条候选（已按 %s/%s 过滤）",
            len(fixes),
            sp,
            fam or "-",
        )
        return fixes, "llm"
    fb = _filt(
        fallback_fix_commands_if_enabled(history, shell_profile=sp),
    )
    if fb:
        return fb, "heuristic"
    return [], "none"


def call_fix_pipeline(
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
) -> List[str]:
    """优先 remediator（KB + few-shot + 结构化 LLM），其次裸 JSON commands，最后启发式。"""
    fixes, _src = call_fix_pipeline_with_source(
        history,
        shell_profile=shell_profile,
        distro_family=distro_family,
        pkg_manager=pkg_manager,
    )
    return fixes


def call_llm_for_fix_commands(
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
    distro_family: Optional[str] = None,
    pkg_manager: Optional[str] = None,
) -> List[str]:
    """调用已配置的 LLM；无 Key 时返回空列表。"""
    mgr = get_llm()
    if not mgr.is_available:
        logger.info("closure_fix: LLM 不可用，跳过修复建议")
        return []
    sp = _normalize_shell_profile(shell_profile)
    messages = [
        {
            "role": "system",
            "content": fix_system_prompt_for_profile(
                sp, distro_family=distro_family, pkg_manager=pkg_manager
            ),
        },
        {
            "role": "user",
            "content": build_fix_user_message(
                history,
                sp,
                distro_family=distro_family,
                pkg_manager=pkg_manager,
            ),
        },
    ]
    try:
        text = mgr.chat(messages, temperature=0.05, max_tokens=512)
    except Exception as ex:  # pragma: no cover
        logger.warning("closure_fix: LLM 调用失败: %s", ex)
        return []
    if not text:
        return []
    return parse_fix_commands_json(text)


def fallback_fix_commands_if_enabled(
    history: List[ClosurePayload],
    *,
    shell_profile: str = "unix",
) -> List[str]:
    """无 LLM 时可选启发式（测试/离线）；需 OPS_CLOSURE_FIX_FALLBACK=1。"""
    import os

    if (os.environ.get("OPS_CLOSURE_FIX_FALLBACK") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return []
    if not history:
        return []
    cp = history[-1]
    err = ((cp.stderr or "") + (cp.stdout or "")).lower()
    cmd = (cp.effective_command or cp.raw_command or "").strip()
    if not cmd:
        return []
    sp = _normalize_shell_profile(shell_profile)
    if "permission denied" in err or "eacces" in err:
        if sp == "unix" and not cmd.startswith("sudo "):
            return [f"sudo {cmd}"]
    # 文件不存在：unix 上勿再硬删；给出幂等确认
    if sp == "unix" and (
        "no such file" in err or "cannot remove" in err or "not found" in err
    ):
        # 从原命令抽路径（极简）
        m = re.search(r"(?i)\brm\s+(?:-[a-zA-Z]+\s+)*['\"]?(\S+?)['\"]?\s*$", cmd)
        path = m.group(1) if m else ""
        if path and path not in ("-f", "-r", "-rf", "--"):
            return [f'test ! -e {path} && echo already_absent || rm -f -- {path}']
    return []
