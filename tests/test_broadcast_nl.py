"""Fleet：按 OS 分段翻译群发意图。"""
from __future__ import annotations

from types import SimpleNamespace

from chibyterm.broadcast_nl import (
    build_fleet_preview,
    commands_by_session_from_preview,
    segment_key_for_session,
)


def test_segment_key_windows_vs_linux():
    assert segment_key_for_session(
        target_os="windows", shell_profile="powershell", conn_type="winrm"
    ).startswith("windows")
    assert segment_key_for_session(
        target_os="linux", shell_profile="unix", conn_type="ssh"
    ).startswith("unix")


def test_resolve_fleet_session_ids_dedupes_same_host():
    from chibyterm.broadcast_nl import resolve_fleet_session_ids

    sessions = {
        "s1": SimpleNamespace(host_id="h1"),
        "s2": SimpleNamespace(host_id="h1"),  # duplicate
        "s3": SimpleNamespace(host_id="h2"),
        "s4": SimpleNamespace(host_id=""),  # no host_id → unique
    }
    kept, info = resolve_fleet_session_ids(
        ["s1", "s2", "s3", "s4"],
        lambda sid: sessions.get(sid),
        dedupe_hosts=True,
        preferred_session_id="s2",
        host_label_fn=lambda sid: sid,
        ui_locale="zh-CN",
    )
    assert kept == ["s2", "s3", "s4"]
    assert info["opened_sessions"] == 4
    assert info["unique_hosts"] == 3
    assert info["merged_sessions"] == 1
    assert info["enabled"] is True
    assert any(g["kept_session_id"] == "s2" for g in info["groups"])


def test_resolve_fleet_session_ids_keep_all():
    from chibyterm.broadcast_nl import resolve_fleet_session_ids

    sessions = {
        "s1": SimpleNamespace(host_id="h1"),
        "s2": SimpleNamespace(host_id="h1"),
    }
    kept, info = resolve_fleet_session_ids(
        ["s1", "s2"],
        lambda sid: sessions.get(sid),
        dedupe_hosts=False,
        ui_locale="zh-CN",
    )
    assert kept == ["s1", "s2"]
    assert info["merged_sessions"] == 0
    assert info["enabled"] is False


def test_build_fleet_preview_dedupes_before_translate():
    sessions = {
        "s1": SimpleNamespace(target_os="linux", host_id="h1"),
        "s2": SimpleNamespace(target_os="linux", host_id="h1"),
        "s3": SimpleNamespace(target_os="windows", host_id="h2"),
    }
    hosts = {
        "h1": SimpleNamespace(conn_type="ssh", tags=[]),
        "h2": SimpleNamespace(conn_type="winrm", tags=[]),
    }
    calls = {"n": 0}

    def process_nl(text, *, shell_profile="unix", runtime_hint="", ui_locale="zh-CN"):
        calls["n"] += 1
        if shell_profile == "powershell":
            return SimpleNamespace(command="Get-Process", explanation="PS")
        return SimpleNamespace(command="ps aux", explanation="unix")

    preview = build_fleet_preview(
        nl_intent="看进程",
        session_ids=["s1", "s2", "s3"],
        get_session=lambda sid: sessions.get(sid),
        host_label_fn=lambda sid: sid,
        runtime_hint_fn=lambda _s: "",
        shell_profile_fn=lambda s: (
            "powershell" if getattr(s, "target_os", "") == "windows" else "unix"
        ),
        process_nl=process_nl,
        ui_locale="zh-CN",
        host_store=hosts,
        dedupe_hosts=True,
        preferred_session_id="s1",
    )
    assert len(preview.targets) == 2
    assert {t.session_id for t in preview.targets} == {"s1", "s3"}
    assert preview.dedupe["merged_sessions"] == 1
    t1 = next(t for t in preview.targets if t.session_id == "s1")
    assert t1.duplicate_tabs == 1
    # 仍按 OS 分段翻译 2 次（linux + windows），不会因重复 Tab 多翻
    assert calls["n"] == 2


def test_build_fleet_preview_splits_os():
    sessions = {
        "s1": SimpleNamespace(target_os="linux", host_id="h1"),
        "s2": SimpleNamespace(target_os="windows", host_id="h2"),
    }
    hosts = {
        "h1": SimpleNamespace(conn_type="ssh", tags=[]),
        "h2": SimpleNamespace(conn_type="winrm", tags=[]),
    }

    def process_nl(text, *, shell_profile="unix", runtime_hint="", ui_locale="zh-CN"):
        if shell_profile == "powershell":
            return SimpleNamespace(command="Get-CimInstance Win32_OperatingSystem", explanation="PS mem")
        return SimpleNamespace(command="free -h", explanation="unix mem")

    preview = build_fleet_preview(
        nl_intent="查看内存",
        session_ids=["s1", "s2"],
        get_session=lambda sid: sessions.get(sid),
        host_label_fn=lambda sid: sid,
        runtime_hint_fn=lambda _s: "",
        shell_profile_fn=lambda s: (
            "powershell" if getattr(s, "target_os", "") == "windows" else "unix"
        ),
        process_nl=process_nl,
        ui_locale="zh-CN",
        host_store=hosts,
    )
    assert len(preview.segments) == 2
    cmds = commands_by_session_from_preview(preview)
    assert cmds["s1"] == "free -h"
    assert cmds["s2"] == "Get-CimInstance Win32_OperatingSystem"
    assert any("Windows" in w or "Unix" in w or "分别" in w for w in preview.warnings)
    assert preview.execution_mode == "session"


def test_build_fleet_preview_from_hosts_oneshot():
    from chibyterm.broadcast_nl import build_fleet_preview_from_hosts

    hosts = {
        "h1": SimpleNamespace(
            id="h1", name="web", host="1.1.1.1", conn_type="ssh", distro_profile=None
        ),
        "h2": SimpleNamespace(
            id="h2", name="win", host="2.2.2.2", conn_type="winrm", distro_profile=None
        ),
    }

    def process_nl(text, *, shell_profile="unix", runtime_hint="", ui_locale="zh-CN"):
        if shell_profile == "powershell":
            return SimpleNamespace(command="Get-Process", explanation="PS")
        return SimpleNamespace(command="uptime", explanation="unix")

    preview = build_fleet_preview_from_hosts(
        nl_intent="查负载",
        host_ids=["h1", "h2", "missing"],
        host_store=hosts,
        process_nl=process_nl,
        ui_locale="zh-CN",
    )
    assert preview.execution_mode == "oneshot"
    assert preview.dedupe.get("opened_sessions") == 0
    assert preview.dedupe.get("unique_hosts") == 2
    cmds = commands_by_session_from_preview(preview)
    assert cmds["h1"] == "uptime"
    assert cmds["h2"] == "Get-Process"
    assert any(t.host_id == "missing" and not t.ok for t in preview.targets)
    assert any("oneshot" in w for w in preview.warnings)
