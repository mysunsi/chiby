"""跨会话 Host Snapshot + remote_rollback 别名。"""

from __future__ import annotations

from terminal.mobile import host_snapshot as hs
from terminal.mobile.hermes_protocol import inject_hermes_continuity
from terminal.mobile.remote_tools import parse_remote_tool_calls


def test_save_load_and_format(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_HOST_SNAPSHOT_DIR", str(tmp_path))
    snap = hs.HostSnapshot(
        host_id="d3af1f4f",
        hostname="yl.sunsi.cn",
        last_seen="2026-07-22T07:48:00Z",
        system=hs.SystemSnapshot(
            os="Windows Server 2022",
            total_ram_gb=3.5,
            free_ram_gb=1.2,
            disk_c_total_gb=39.5,
            disk_c_free_gb=11.72,
            cpu_usage_pct=23,
        ),
        services={
            "nginx": hs.ServiceStatus(status="running", pid=1234, memory_mb=45),
        },
        recent_changes=[
            hs.FileChange(
                path=r"C:\Open\Api\src\middleware.py",
                action="rewrite",
                ts="2026-07-22T07:45:00Z",
            )
        ],
        last_tasks=["代码审查 C:\\Open\\Api"],
    )
    assert hs.save_snapshot(snap) is True
    loaded = hs.load_snapshot("d3af1f4f")
    assert loaded is not None
    assert loaded.hostname == "yl.sunsi.cn"
    assert loaded.system.free_ram_gb == 1.2
    assert loaded.services["nginx"].pid == 1234
    text = hs.format_for_prompt(loaded)
    assert text.startswith("[Host Snapshot: d3af1f4f")
    assert "yl.sunsi.cn" in text
    assert "RAM" in text
    assert "nginx=running" in text
    assert "middleware.py" in text
    assert len(text) <= 1500


def test_append_change_and_remember_tasks(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_HOST_SNAPSHOT_DIR", str(tmp_path))
    hs.append_file_change(
        "h1",
        r"C:\Open\Api\src\a.py",
        "rewrite",
        hostname="demo",
    )
    hs.append_file_change("h1", r"C:\Open\Api\src\b.py", "delete")
    for i in range(25):
        hs.append_file_change("h1", f"/tmp/f{i}.txt", "rewrite")
    snap = hs.load_snapshot("h1")
    assert snap is not None
    assert len(snap.recent_changes) == 20
    hs.remember_tasks("h1", ["修 JWT", "限流 Redis", "修 JWT", "x"])
    snap2 = hs.load_snapshot("h1")
    assert snap2 is not None
    assert snap2.last_tasks[0] == "修 JWT"
    assert "限流 Redis" in snap2.last_tasks
    assert len(snap2.last_tasks) <= 8


def test_format_truncates(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_HOST_SNAPSHOT_DIR", str(tmp_path))
    snap = hs.HostSnapshot(
        host_id="h1",
        last_tasks=["task-" + ("x" * 200)] * 8,
        recent_changes=[
            hs.FileChange(path="/a/" + ("p" * 80) + f"{i}.py", action="rewrite", ts="t")
            for i in range(10)
        ],
    )
    text = hs.format_for_prompt(snap, max_chars=200)
    assert len(text) <= 200
    assert text.endswith("…") or len(text) < 200


def test_format_marks_empty_memory_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_HOST_SNAPSHOT_DIR", str(tmp_path))
    snap = hs.HostSnapshot(
        host_id="40e11445",
        hostname="Debian",
        last_seen="2026-07-28T03:17:04Z",
        system=hs.SystemSnapshot(),
        last_tasks=["连接失败提示"],
    )
    avail = hs.snapshot_availability(snap)
    assert avail["memory_available"] is False
    assert avail["disk_available"] is False
    text = hs.format_for_prompt(snap)
    assert "memory_available: false" in text
    assert "disk_available: false" in text
    assert "禁止" in text
    assert "RAM " not in text  # 空内存不得伪装成有数值

    missing = hs.load_snapshot_prompt("no-such-host-zzz")
    assert "data_available: false" in missing
    assert "memory_available: false" in missing


def test_inject_drops_foreign_host_continuity():
    out = inject_hermes_continuity(
        "内存还剩多少",
        last_offer="main 内存 3.7Gi 可用 695Mi",
        continuity_host_id="5d418c8e",
        current_host_id="40e11445",
        host_snapshot_text="[Host Snapshot: 40e11445]\nmemory_available: false",
    )
    assert "已完成" not in out
    assert "旧机" in out or "作废" in out
    assert "40e11445" in out


def test_host_switch_void_notice():
    from terminal.mobile.hermes_protocol import format_host_switch_void_notice

    text = format_host_switch_void_notice(
        old_host_id="5d418c8e",
        old_label="main.sunsi.cn",
        new_host_id="40e11445",
        new_label="Debian",
        discarded=["上一轮结论/追问", "内存诊断结论"],
    )
    assert "工作台已切换" in text
    assert "5d418c8e" in text and "40e11445" in text
    assert "内存诊断结论" in text
    assert "重新执行查询" in text


def test_inject_continuity_prepends_host_snapshot():
    out = inject_hermes_continuity(
        "继续查磁盘",
        last_user="看磁盘",
        host_snapshot_text="[Host Snapshot: h1]\nLast seen: t\nSystem: RAM 1/2GB free",
        current_host_id="h1",
        continuity_host_id="h1",
    )
    assert out.startswith("[Host Snapshot: h1]")
    assert "[会话续接]" in out
    assert "[用户本轮]" in out
    # 回灌信封不注入
    fb = "[掌上AI机房 · 远端工具结果回灌 · exec_path=a2]\n{}"
    assert (
        inject_hermes_continuity(fb, host_snapshot_text="[Host Snapshot: h1]") == fb
    )


def test_remote_rollback_alias():
    text = (
        "<<<REMOTE_TOOL>>>\n"
        '{"tool":"remote_rollback","host":"h1","path":"/tmp/a.py"}\n'
        "<<<END_REMOTE_TOOL>>>\n"
    )
    calls = parse_remote_tool_calls(text)
    assert len(calls) == 1
    assert calls[0].tool == "remote_restore"
    assert calls[0].path == "/tmp/a.py"
