# -*- coding: utf-8 -*-
from terminal.mobile.host_display import (
    host_display_label,
    humanize_host_ids_in_text,
    label_from_host_obj,
)
from terminal.mobile.job_orchestrator import JobRun, TaskRun, format_job_feedback_for_hermes
from terminal.mobile.models import HostSummary
from terminal.mobile.remote_tools import RemoteToolResult, format_tool_results_for_user


def test_host_display_prefers_name_and_addr():
    assert host_display_label("d3af1f4f", name="Win桌面", host="yl.sunsi.cn") == "Win桌面 · yl.sunsi.cn"
    assert host_display_label("d3af1f4f", name="", host="yl.sunsi.cn") == "yl.sunsi.cn"
    assert host_display_label("d3af1f4f", name="d3af1f4f", host="yl.sunsi.cn") == "yl.sunsi.cn"
    assert host_display_label("d3af1f4f") == "d3af1f4f"


def test_format_tool_results_uses_lookup():
    class H:
        id = "d3af1f4f"
        name = "Win桌面"
        host = "yl.sunsi.cn"

    def lookup(hid: str):
        return H() if hid == "d3af1f4f" else None

    r = RemoteToolResult(
        tool="remote_remove",
        host="d3af1f4f",
        ok=True,
        exit_code=0,
        command="remote_remove C:\\x.rdp",
        stdout="deleted",
    )
    text = format_tool_results_for_user([r], host_lookup=lookup)
    assert "[Win桌面 · yl.sunsi.cn]" in text
    assert "[d3af1f4f]" not in text


def test_label_from_dict():
    assert "yl.sunsi.cn" in label_from_host_obj(
        {"id": "d3af1f4f", "name": "", "host": "yl.sunsi.cn"},
        "d3af1f4f",
    )


def test_humanize_bare_short_ids_in_conclusion():
    hosts = [
        HostSummary(id="d3af1f4f12ab", name="Win桌面", host="yl.sunsi.cn", conn_type="winrm"),
        HostSummary(id="5d418c8e99cc", name="Ubuntu机", host="10.0.0.2", conn_type="ssh"),
    ]
    src = (
        "| 项目 | d3af1f4f (Windows Server) | 5d418c8e (Ubuntu 20.04) |\n"
        "| --- | --- | --- |\n"
        "| 物理内存 | 4GB | 3.7GB |\n\n"
        "### d3af1f4f (Windows 4GB)\n偏紧但够用\n"
        "- **d3af1f4f**：无需立即扩容\n"
        "- **5d418c8e**：建议清理缓存\n"
    )
    out = humanize_host_ids_in_text(src, hosts)
    assert "d3af1f4f" not in out
    assert "5d418c8e" not in out
    assert "Win桌面" in out
    assert "Ubuntu机" in out


def test_hermes_feedback_uses_display_name_not_raw_id():
    run = JobRun(
        run_id="run_x",
        name="内存",
        host_ids=["d3af1f4f12ab"],
        commands=["free -h"],
        tasks=[
            TaskRun(
                task_id="t1",
                host_id="d3af1f4f12ab",
                host_label="Win桌面 · yl.sunsi.cn",
                commands=["free -h"],
                status="ok",
                stdout_tail="ok",
            )
        ],
    )
    pack = format_job_feedback_for_hermes(run)
    assert "### Win桌面 · yl.sunsi.cn" in pack
    assert "d3af1f4f12ab" not in pack
    assert "display_name" in pack
    assert "禁止出现裸 host_id" in pack
