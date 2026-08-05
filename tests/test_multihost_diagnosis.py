"""多机排查：聚合、HostScopeView、单机降级提示。"""
from __future__ import annotations

from chibyterm.context_units.host_targets import HostScopeView, apply_host_targets_to_state
from chibyterm.models.session import ConversationState
from chibyterm.multihost_diag import (
    HostDiagRaw,
    aggregate_generic_results,
    aggregate_process_results,
    build_multihost_prompt_block,
    detect_single_host_followup,
    diag_command,
)


def test_host_scope_view_display_with_group():
    view = HostScopeView(
        host_ids=["a", "b", "c"],
        group_id="prod-web",
        group_name="生产-Web",
    )
    assert view.host_count == 3
    assert view.display_name == "生产-Web（3台）"
    d = view.to_dict()
    assert d["group_id"] == "prod-web"
    assert d["group_name"] == "生产-Web"


def test_host_scope_view_without_group():
    view = HostScopeView(host_ids=["x", "y"])
    assert view.display_name == "已选 2 台主机"
    empty = HostScopeView(host_ids=[])
    assert empty.display_name == "未选择主机"


def test_apply_host_targets_group_fields():
    st = ConversationState(conversation_id="c1")
    apply_host_targets_to_state(
        st,
        host_ids=["h1", "h2"],
        group_id="g1",
        group_name="生产-Web",
    )
    assert st.ui_host_ids == ["h1", "h2"]
    assert st.ui_host_group_id == "g1"
    assert st.ui_host_group_name == "生产-Web"
    apply_host_targets_to_state(st, host_ids=[])
    assert st.ui_host_ids == []
    assert st.ui_host_group_id == ""
    assert st.ui_host_group_name == ""


def test_aggregate_process_results_common_and_outlier():
    raws = [
        HostDiagRaw(
            host_id="h1",
            host_label="web-1",
            ok=True,
            stdout=(
                "USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
                "root         1  5.0  1.0  10000  2000 ?        Ss   00:00   0:01 /usr/bin/nginx\n"
                "root         2  3.0  0.5   8000  1000 ?        Ss   00:00   0:00 /usr/bin/sshd\n"
            ),
        ),
        HostDiagRaw(
            host_id="h2",
            host_label="web-2",
            ok=True,
            stdout=(
                "USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\n"
                "root         1 40.0  2.0  10000  2000 ?        Ss   00:00   0:10 /usr/bin/nginx\n"
                "root         2  2.0  0.5   8000  1000 ?        Ss   00:00   0:00 /usr/bin/sshd\n"
            ),
        ),
        HostDiagRaw(
            host_id="h3",
            host_label="web-3",
            ok=False,
            error="timeout",
        ),
    ]
    agg = aggregate_process_results(raws, limit=8)
    d = agg.to_dict()
    assert d["tool"] == "process_list"
    assert d["successful_hosts"] == ["h1", "h2"]
    assert d["failed_hosts"] == ["h3"]
    assert "nginx" in d["summary"]["common_processes"]
    assert d["summary"]["max_cpu_host"] == "web-2"
    assert any(o.get("host_id") == "h2" for o in d["summary"]["outliers"])


def test_aggregate_generic_service_status():
    raws = [
        HostDiagRaw(host_id="a", host_label="A", ok=True, stdout="failed unit x\n"),
        HostDiagRaw(host_id="b", host_label="B", ok=False, error="denied"),
    ]
    agg = aggregate_generic_results("service_status", raws)
    assert agg.successful_hosts == ["a"]
    assert agg.failed_hosts == ["b"]
    assert "failed unit" in (agg.per_host["a"].get("raw_preview") or "")


def test_diag_command_linux_and_windows():
    assert "ps aux" in diag_command("process_list", conn_type="ssh")
    assert "Get-Process" in diag_command("process_list", conn_type="winrm")
    assert "systemctl" in diag_command("service_status", conn_type="ssh")
    assert "ss -tunap" in diag_command("network_connections", conn_type="ssh")
    assert "journalctl" in diag_command("log_search", conn_type="ssh", pattern="oom")


def test_build_multihost_prompt_multi_vs_single():
    multi = build_multihost_prompt_block(
        display_name="生产-Web（3台）",
        host_labels=["a", "b", "c"],
        multi=True,
    )
    assert "总体态势" in multi
    assert "异常离群点" in multi
    single = build_multihost_prompt_block(
        display_name="已选 1 台主机",
        host_labels=["only"],
        multi=False,
    )
    assert "无需横向对比" in single
    assert "总体态势" not in single


def test_detect_single_host_followup():
    labels = {"id-a": "web-prod-1", "id-b": "web-prod-2"}
    assert detect_single_host_followup("web-prod-2 的 CPU 为什么高", labels) == "id-b"
    assert detect_single_host_followup("都正常吗", labels) is None
