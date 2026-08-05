"""集群群发（Fleet）：自然语言意图 → 按会话 OS/Shell 翻译为异构命令。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FleetTargetPreview:
    session_id: str
    host_label: str = ""
    host_id: str = ""
    target_os: str = ""
    shell_profile: str = "unix"  # unix | powershell
    segment_key: str = ""
    command: str = ""
    explanation: str = ""
    ok: bool = False
    error: str = ""
    duplicate_tabs: int = 0  # 同 host 被合并掉的其它 Tab 数


@dataclass
class FleetPreviewResult:
    nl_intent: str
    targets: List[FleetTargetPreview] = field(default_factory=list)
    segments: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dedupe: Dict[str, Any] = field(default_factory=dict)
    # session = 经打开终端下发；oneshot = 按主机独立连接（不强制开 Tab）
    execution_mode: str = "session"

    def to_api_dict(self) -> Dict[str, Any]:
        return {
            "nl_intent": self.nl_intent,
            "execution_mode": self.execution_mode or "session",
            "warnings": list(self.warnings),
            "segments": list(self.segments),
            "dedupe": dict(self.dedupe or {}),
            "targets": [
                {
                    "session_id": t.session_id,
                    "host_label": t.host_label,
                    "host_id": t.host_id,
                    "target_os": t.target_os,
                    "shell_profile": t.shell_profile,
                    "segment_key": t.segment_key,
                    "command": t.command,
                    "explanation": t.explanation,
                    "ok": t.ok,
                    "error": t.error,
                    "duplicate_tabs": int(t.duplicate_tabs or 0),
                }
                for t in self.targets
            ],
            "ok_count": sum(1 for t in self.targets if t.ok and t.command),
            "fail_count": sum(1 for t in self.targets if not (t.ok and t.command)),
        }


def resolve_fleet_session_ids(
    session_ids: Sequence[str],
    get_session: Callable[[str], Any],
    *,
    dedupe_hosts: bool = True,
    preferred_session_id: Optional[str] = None,
    host_label_fn: Optional[Callable[[str], str]] = None,
    ui_locale: str = "zh-CN",
) -> Tuple[List[str], Dict[str, Any]]:
    """按 host_id 去重打开的终端；无 host_id 的会话各自独立（不合并）。

    保留规则：同 host 优先 preferred_session_id，否则保留首次出现顺序。
    """
    label_fn = host_label_fn or (lambda sid: sid)
    opened = [str(x) for x in session_ids if str(x).strip()]
    info: Dict[str, Any] = {
        "enabled": bool(dedupe_hosts),
        "opened_sessions": len(opened),
        "unique_hosts": 0,
        "merged_sessions": 0,
        "groups": [],
    }
    if not opened:
        return [], info
    if not dedupe_hosts:
        # 不去重：每个会话视为独立目标
        seen_hosts = set()
        for sid in opened:
            sess = get_session(sid)
            hid = str(getattr(sess, "host_id", None) or "").strip() if sess else ""
            key = hid or f"__sid__:{sid}"
            seen_hosts.add(key)
        info["unique_hosts"] = len(seen_hosts)
        return list(opened), info

    # host_key → {kept, skipped: [{session_id, label}], host_id, host_label}
    groups_map: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []

    def _ensure(key: str, *, host_id: str, host_label: str) -> Dict[str, Any]:
        if key not in groups_map:
            groups_map[key] = {
                "host_id": host_id,
                "host_label": host_label,
                "kept_session_id": "",
                "skipped": [],
            }
            order.append(key)
        return groups_map[key]

    pref = str(preferred_session_id or "").strip()

    for sid in opened:
        sess = get_session(sid)
        hid = str(getattr(sess, "host_id", None) or "").strip() if sess else ""
        # 无 host_id：不与其它会话合并
        key = hid if hid else f"__sid__:{sid}"
        lab = label_fn(sid)
        g = _ensure(key, host_id=hid, host_label=lab if hid else lab)
        if not g["kept_session_id"]:
            g["kept_session_id"] = sid
            if hid:
                g["host_label"] = lab
            continue
        # 已有保留会话：若当前是 preferred，则替换
        if pref and sid == pref and g["kept_session_id"] != pref:
            prev = g["kept_session_id"]
            g["skipped"].append({"session_id": prev, "label": label_fn(prev)})
            g["kept_session_id"] = sid
            if hid:
                g["host_label"] = lab
        else:
            g["skipped"].append({"session_id": sid, "label": lab})

    # preferred 可能在列表中靠后：上面循环已处理替换
    kept: List[str] = []
    groups_out: List[Dict[str, Any]] = []
    merged = 0
    for key in order:
        g = groups_map[key]
        kept.append(g["kept_session_id"])
        skipped = list(g["skipped"])
        merged += len(skipped)
        if skipped or g.get("host_id"):
            groups_out.append(
                {
                    "host_id": g["host_id"],
                    "host_label": g["host_label"],
                    "kept_session_id": g["kept_session_id"],
                    "skipped": skipped,
                    "duplicate_tabs": len(skipped),
                }
            )

    info["unique_hosts"] = len(kept)
    info["merged_sessions"] = merged
    info["groups"] = [g for g in groups_out if g.get("duplicate_tabs")]

    if merged > 0:
        if str(ui_locale).startswith("en"):
            info["summary"] = (
                f"{info['unique_hosts']} host(s) from {info['opened_sessions']} open tabs "
                f"(merged {merged} duplicate tab(s))."
            )
        elif str(ui_locale).startswith("zh-TW"):
            info["summary"] = (
                f"目標 {info['unique_hosts']} 台主機"
                f"（已開啟 {info['opened_sessions']} 個終端，合併重複 {merged} 個）"
            )
        else:
            info["summary"] = (
                f"目标 {info['unique_hosts']} 台主机"
                f"（已打开 {info['opened_sessions']} 个终端，合并重复 {merged} 个）"
            )
    else:
        info["summary"] = ""

    return kept, info


def segment_key_for_session(
    *,
    target_os: str,
    shell_profile: str,
    conn_type: str = "",
) -> str:
    """按 OS / Shell 分段，避免 Win/Linux 共用一条命令。"""
    sp = (shell_profile or "unix").strip().lower()
    tos = (target_os or "").strip().lower()
    ct = (conn_type or "").strip().lower()
    if sp == "powershell" or tos == "windows" or ct == "winrm":
        return "windows_powershell"
    if tos == "macos":
        return "unix_macos"
    if tos == "wsl":
        return "unix_wsl"
    if tos == "freebsd":
        return "unix_freebsd"
    return "unix_linux"


def build_fleet_preview(
    *,
    nl_intent: str,
    session_ids: Sequence[str],
    get_session: Callable[[str], Any],
    host_label_fn: Callable[[str], str],
    runtime_hint_fn: Callable[[Any], str],
    shell_profile_fn: Callable[[Any], str],
    process_nl: Callable[..., Any],
    ui_locale: str = "zh-CN",
    host_store: Optional[Dict[str, Any]] = None,
    dedupe_hosts: bool = True,
    preferred_session_id: Optional[str] = None,
) -> FleetPreviewResult:
    """
    将同一 NL 意图按 shell 分段翻译。

    默认按 host_id 去重打开的终端（同主机多 Tab 只保留一个执行目标）。
    process_nl(nl, shell_profile=, runtime_hint=, ui_locale=) → 对象需含 command/explanation
    """
    intent = (nl_intent or "").strip()
    out = FleetPreviewResult(nl_intent=intent, execution_mode="session")
    if not intent:
        out.warnings.append("意图为空")
        return out
    if not session_ids:
        out.warnings.append("无已打开会话")
        return out

    kept_ids, dedupe_info = resolve_fleet_session_ids(
        session_ids,
        get_session,
        dedupe_hosts=dedupe_hosts,
        preferred_session_id=preferred_session_id,
        host_label_fn=host_label_fn,
        ui_locale=ui_locale,
    )
    out.dedupe = dedupe_info
    dup_by_kept = {
        str(g.get("kept_session_id") or ""): int(g.get("duplicate_tabs") or 0)
        for g in (dedupe_info.get("groups") or [])
    }
    if dedupe_info.get("summary"):
        out.warnings.append(str(dedupe_info["summary"]))

    # session_id → meta（仅保留去重后的会话）
    metas: List[Dict[str, Any]] = []
    for sid in kept_ids:
        sess = get_session(sid)
        if not sess:
            out.targets.append(
                FleetTargetPreview(
                    session_id=sid,
                    host_label=host_label_fn(sid),
                    error="会话不存在",
                )
            )
            continue
        tos = str(getattr(sess, "target_os", None) or "").strip().lower()
        sp = shell_profile_fn(sess)
        host_id = str(getattr(sess, "host_id", None) or "")
        conn = ""
        if host_store and host_id and host_id in host_store:
            h = host_store[host_id]
            ct = getattr(h, "conn_type", None)
            conn = str(getattr(ct, "value", ct) or "").lower()
        sk = segment_key_for_session(target_os=tos, shell_profile=sp, conn_type=conn)
        metas.append(
            {
                "session_id": sid,
                "session": sess,
                "host_id": host_id,
                "host_label": host_label_fn(sid),
                "target_os": tos or "unknown",
                "shell_profile": sp,
                "segment_key": sk,
                "runtime_hint": runtime_hint_fn(sess),
                "duplicate_tabs": dup_by_kept.get(sid, 0),
            }
        )

    # 按 segment_key 分组；每组用代表会话的 runtime_hint 翻译一次
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for m in metas:
        groups.setdefault(m["segment_key"], []).append(m)

    for sk, members in groups.items():
        sample = members[0]
        sp = sample["shell_profile"]
        hint = sample["runtime_hint"] or ""
        # 强化跨 OS 约束
        hint_extra = (
            f"Fleet segment={sk}; shell_profile={sp}. "
            "Generate ONLY a command valid for this OS/shell. "
            "Never emit Linux-only commands for PowerShell/Windows, "
            "nor PowerShell-only cmdlets for unix shells."
        )
        combined_hint = (hint + "\n" + hint_extra).strip()
        cmd = ""
        expl = ""
        err = ""
        # 强制走「意图翻译」，避免裸命令透传导致 Win/Linux 共用同一条
        translate_prompt = (
            f"用户运维意图：{intent}\n"
            f"请输出一条适合当前环境（shell_profile={sp}，segment={sk}）的可执行命令；"
            f"不要照搬其它操作系统的命令。"
        )
        try:
            pr = process_nl(
                translate_prompt,
                shell_profile=sp,
                runtime_hint=combined_hint,
                ui_locale=ui_locale,
            )
            cmd = str(getattr(pr, "command", None) or "").strip()
            expl = str(getattr(pr, "explanation", None) or "").strip()[:400]
            if not cmd:
                err = "翻译结果无命令"
        except Exception as exc:
            logger.warning("fleet NL translate failed seg=%s: %s", sk, exc)
            err = str(exc)[:300]

        out.segments.append(
            {
                "segment_key": sk,
                "shell_profile": sp,
                "host_count": len(members),
                "command": cmd,
                "explanation": expl,
                "ok": bool(cmd) and not err,
                "error": err,
            }
        )
        for m in members:
            out.targets.append(
                FleetTargetPreview(
                    session_id=m["session_id"],
                    host_label=m["host_label"],
                    host_id=str(m.get("host_id") or ""),
                    target_os=m["target_os"],
                    shell_profile=sp,
                    segment_key=sk,
                    command=cmd if not err else "",
                    explanation=expl,
                    ok=bool(cmd) and not err,
                    error=err,
                    duplicate_tabs=int(m.get("duplicate_tabs") or 0),
                )
            )

    # 若同时存在 windows 与 unix 分段，给一条提示（非阻断）
    keys = {s["segment_key"] for s in out.segments}
    has_win = any(k.startswith("windows") for k in keys)
    has_unix = any(k.startswith("unix") for k in keys)
    if has_win and has_unix:
        out.warnings.append(
            "已按 Windows / Unix 分别生成命令，请确认后再下发。"
            if not str(ui_locale).startswith("en")
            else "Commands were generated per Windows/Unix segment — review before dispatch."
        )
    return out


def commands_by_session_from_preview(preview: FleetPreviewResult) -> Dict[str, str]:
    return {
        t.session_id: t.command
        for t in preview.targets
        if t.ok and (t.command or "").strip()
    }


def _host_conn_type(h: Any) -> str:
    ct = getattr(h, "conn_type", None)
    return str(getattr(ct, "value", ct) or "ssh").strip().lower()


def _host_label(h: Any) -> str:
    name = str(getattr(h, "name", "") or "").strip()
    addr = str(getattr(h, "host", "") or "").strip()
    if name and addr:
        return f"{name} ({addr})"
    return name or addr or str(getattr(h, "id", "") or "")


def build_fleet_preview_from_hosts(
    *,
    nl_intent: str,
    host_ids: Sequence[str],
    host_store: Dict[str, Any],
    process_nl: Callable[..., Any],
    ui_locale: str = "zh-CN",
) -> FleetPreviewResult:
    """按主机目录分段翻译 NL（oneshot 执行路径，不依赖打开终端）。

    targets.session_id 填 host_id，便于与 commands_by_session / 进度卡复用同一套字段。
    """
    intent = (nl_intent or "").strip()
    out = FleetPreviewResult(nl_intent=intent, execution_mode="oneshot")
    if not intent:
        out.warnings.append("意图为空")
        return out

    metas: List[Dict[str, Any]] = []
    missing: List[str] = []
    for hid in host_ids:
        hid_s = str(hid or "").strip()
        if not hid_s:
            continue
        h = host_store.get(hid_s) if host_store else None
        if h is None:
            missing.append(hid_s)
            out.targets.append(
                FleetTargetPreview(
                    session_id=hid_s,
                    host_id=hid_s,
                    host_label=hid_s,
                    error="主机不存在",
                )
            )
            continue
        conn = _host_conn_type(h)
        sp = "powershell" if conn == "winrm" else "unix"
        tos = "windows" if conn == "winrm" else "linux"
        sk = segment_key_for_session(target_os=tos, shell_profile=sp, conn_type=conn)
        # 发行版指纹可进 hint
        dp = getattr(h, "distro_profile", None)
        fam = ""
        if dp is not None:
            fam = str(getattr(dp, "family", None) or getattr(dp, "pretty_name", None) or "")
        hint = f"host={getattr(h, 'host', '')}; conn_type={conn}"
        if fam:
            hint += f"; distro={fam}"
        metas.append(
            {
                "session_id": hid_s,  # oneshot：用 host_id 占位
                "host_id": hid_s,
                "host_label": _host_label(h),
                "target_os": tos,
                "shell_profile": sp,
                "segment_key": sk,
                "runtime_hint": hint,
                "duplicate_tabs": 0,
            }
        )

    n = len(metas)
    out.dedupe = {
        "enabled": False,
        "opened_sessions": 0,
        "unique_hosts": n,
        "merged_sessions": 0,
        "groups": [],
        "summary": (
            f"目标 {n} 台主机（oneshot，不打开终端）"
            if not str(ui_locale).startswith("en")
            else f"{n} host(s) via oneshot (no terminal tabs)"
        ),
    }
    if out.dedupe["summary"]:
        out.warnings.append(str(out.dedupe["summary"]))
    if missing:
        tip = (
            f"有 {len(missing)} 台主机在目录中不存在，已跳过"
            if not str(ui_locale).startswith("en")
            else f"{len(missing)} host id(s) missing from catalog"
        )
        out.warnings.append(tip)
    if not metas:
        if not out.warnings:
            out.warnings.append("无有效主机")
        return out

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for m in metas:
        groups.setdefault(m["segment_key"], []).append(m)

    for sk, members in groups.items():
        sample = members[0]
        sp = sample["shell_profile"]
        hint = sample["runtime_hint"] or ""
        hint_extra = (
            f"Fleet segment={sk}; shell_profile={sp}; execution=oneshot. "
            "Generate ONLY a command valid for this OS/shell. "
            "Never emit Linux-only commands for PowerShell/Windows, "
            "nor PowerShell-only cmdlets for unix shells."
        )
        combined_hint = (hint + "\n" + hint_extra).strip()
        cmd = ""
        expl = ""
        err = ""
        translate_prompt = (
            f"用户运维意图：{intent}\n"
            f"请输出一条适合当前环境（shell_profile={sp}，segment={sk}）的可执行命令；"
            f"不要照搬其它操作系统的命令。"
        )
        try:
            pr = process_nl(
                translate_prompt,
                shell_profile=sp,
                runtime_hint=combined_hint,
                ui_locale=ui_locale,
            )
            cmd = str(getattr(pr, "command", None) or "").strip()
            expl = str(getattr(pr, "explanation", None) or "").strip()[:400]
            if not cmd:
                err = "翻译结果无命令"
        except Exception as exc:
            logger.warning("fleet host NL translate failed seg=%s: %s", sk, exc)
            err = str(exc)[:300]

        out.segments.append(
            {
                "segment_key": sk,
                "shell_profile": sp,
                "host_count": len(members),
                "command": cmd,
                "explanation": expl,
                "ok": bool(cmd) and not err,
                "error": err,
            }
        )
        for m in members:
            out.targets.append(
                FleetTargetPreview(
                    session_id=m["session_id"],
                    host_label=m["host_label"],
                    host_id=str(m.get("host_id") or ""),
                    target_os=m["target_os"],
                    shell_profile=sp,
                    segment_key=sk,
                    command=cmd if not err else "",
                    explanation=expl,
                    ok=bool(cmd) and not err,
                    error=err,
                    duplicate_tabs=0,
                )
            )

    keys = {s["segment_key"] for s in out.segments}
    has_win = any(k.startswith("windows") for k in keys)
    has_unix = any(k.startswith("unix") for k in keys)
    if has_win and has_unix:
        out.warnings.append(
            "已按 Windows / Unix 分别生成命令，请确认后再下发。"
            if not str(ui_locale).startswith("en")
            else "Commands were generated per Windows/Unix segment — review before dispatch."
        )
    return out
