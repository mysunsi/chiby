"""高级模式：诊断 → 修复权限评估 → 受控变更确认。"""

from terminal.mobile.hermes_protocol import (
    advanced_protocol_preamble,
    detect_remediation_signals,
    feedback_protocol_footer,
)
from terminal.mobile.models import ExecResult
from terminal.mobile.orchestrator import (
    _format_exec_feedback_for_hermes,
    _is_controlled_mutate,
    _sanitize_protocol_cmds_detailed,
)


def test_detect_permission_denied_and_nginx_emerg():
    assert detect_remediation_signals(
        "nginx: the configuration file /etc/nginx/nginx.conf syntax is ok\n"
        "nginx: [emerg] open() \"/etc/letsencrypt/live/main.sunsi.cn/fullchain.pem\" "
        "failed (13: Permission denied)"
    )
    assert detect_remediation_signals("cannot open /var/log/nginx/error.log")
    assert detect_remediation_signals("证书无法加载：权限拒绝")
    assert not detect_remediation_signals("nginx is running; active (running)")
    assert not detect_remediation_signals("No such file or directory")


def test_controlled_mutate_includes_chmod_chown_nginx_reload():
    assert _is_controlled_mutate("chmod 644 /etc/letsencrypt/live/x/fullchain.pem")
    assert _is_controlled_mutate("sudo chown www-data:www-data /var/log/nginx/error.log")
    assert _is_controlled_mutate("setfacl -m u:www-data:r /etc/letsencrypt/live/x/fullchain.pem")
    assert _is_controlled_mutate("nginx -s reload")
    assert _is_controlled_mutate("systemctl reload nginx")
    assert not _is_controlled_mutate("rm -rf /")
    assert not _is_controlled_mutate("reboot")


def test_sanitize_routes_chmod_to_mutate_not_auto_readonly():
    kept, mut, rej = _sanitize_protocol_cmds_detailed(
        [
            "ls -l /etc/letsencrypt/live/main.sunsi.cn/fullchain.pem",
            "chmod 644 /etc/letsencrypt/live/main.sunsi.cn/fullchain.pem",
            "nginx -t",
            "nginx -s reload",
        ],
        conn_type="ssh",
    )
    assert any("ls -l" in c for c in kept)
    assert any("nginx -t" in c for c in kept)
    assert any("chmod" in c for c in mut)
    assert any("nginx -s reload" in c for c in mut)
    assert rej == []


def test_sanitize_allows_systemctl_readonly_status_queries():
    """nginx 状态类只读不得被误判为「变更类命令已拦截」。"""
    cmd = "systemctl is-active nginx && systemctl status nginx --no-pager -l"
    kept, mut, rej = _sanitize_protocol_cmds_detailed([cmd], conn_type="ssh")
    assert kept == [cmd]
    assert mut == []
    assert rej == []
    for c in (
        "systemctl is-active nginx",
        "systemctl status nginx --no-pager -l",
        "systemctl is-enabled nginx",
        "systemctl show nginx",
    ):
        k, m, r = _sanitize_protocol_cmds_detailed([c], conn_type="ssh")
        assert k == [c] and m == [] and r == []
    k2, m2, r2 = _sanitize_protocol_cmds_detailed(
        ["systemctl restart nginx"], conn_type="ssh",
    )
    assert k2 == [] and m2 == ["systemctl restart nginx"] and r2 == []


def test_feedback_injects_repair_eval_on_permission_denied():
    text = _format_exec_feedback_for_hermes(
        host_id="main.sunsi.cn",
        conn_type="ssh",
        results=[
            ExecResult(
                ok=False,
                host_id="main.sunsi.cn",
                command="nginx -t",
                exit_code=1,
                stdout_tail="",
                stderr_tail=(
                    'nginx: [emerg] open() "/etc/letsencrypt/live/x/fullchain.pem" '
                    "failed (13: Permission denied)"
                ),
            ),
        ],
        round_idx=2,
    )
    assert "修复权限评估" in text
    assert "禁止" in text and "done=true" in text
    assert "chmod" in text.lower() or "chown" in text.lower()
    footer = feedback_protocol_footer(round_idx=2, remediation_hint=True)
    assert "修复权限评估" in footer


def test_advanced_preamble_mentions_repair_eval_chain():
    p = advanced_protocol_preamble(host_id="h1", conn_type="ssh")
    assert "修复权限评估" in p
    assert "chmod" in p
    assert "最小风险" in p
    assert "语言铁律" in p
    assert "简体中文" in p
    assert "斜杠命令铁律" in p
    assert "中文只写在对用户可见的正文里" not in p


def test_advanced_preamble_injects_distro_family():
    from terminal.distro_profile import DistroProfile

    dp = DistroProfile(
        family="debian",
        pretty_name="Ubuntu 22.04.4 LTS",
        pkg_manager="apt",
        init_system="systemd",
        probed_at="2099-01-01T00:00:00+00:00",
    )
    p = advanced_protocol_preamble(host_id="h1", conn_type="ssh", distro_profile=dp)
    assert "发行版命令族" in p
    assert "family=debian" in p
    assert "apt" in p
    assert "勿用 yum" in p or "勿用 yum/dnf" in p


def test_build_multi_host_prompt_includes_pkg_manager():
    from terminal.distro_profile import DistroProfile
    from terminal.mobile.hermes_protocol import build_multi_host_hermes_prompt

    class H:
        def __init__(self, hid, name, host, ct, dp=None):
            self.id = hid
            self.name = name
            self.host = host
            self.conn_type = ct
            self.distro_profile = dp

    hosts = [
        H(
            "u1",
            "ubuntu",
            "1.1.1.1",
            "ssh",
            DistroProfile(family="debian", pretty_name="Ubuntu 22.04", pkg_manager="apt"),
        ),
        H(
            "r1",
            "rocky",
            "2.2.2.2",
            "ssh",
            DistroProfile(family="rhel", pretty_name="Rocky Linux 9", pkg_manager="dnf"),
        ),
    ]
    text = build_multi_host_hermes_prompt("巡检 nginx", host_ids=["u1", "r1"], hosts=hosts)
    assert "apt" in text and "dnf" in text
    assert "Ubuntu 22.04" in text
