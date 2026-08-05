"""运维/高效型：高危须确认；只读可直跑。删除与停服务对齐确认卡高风险。"""

from terminal.mobile.orchestrator import (
    _ops_cmd_is_high_risk,
    _ops_plan_needs_confirm,
)


def test_single_file_delete_rm_and_remove_item_high_risk():
    """单文件删除也属高危，须确认（不再静默直跑）。"""
    assert _ops_cmd_is_high_risk("rm abc.dat")
    assert _ops_cmd_is_high_risk("rm ~/run.sh")
    assert _ops_cmd_is_high_risk('Remove-Item -Path "C:\\nginx.zip" -Force')
    assert _ops_plan_needs_confirm(["rm abc.dat"])
    assert _ops_plan_needs_confirm(['Remove-Item -Path "C:\\nginx.zip" -Force'])


def test_recursive_delete_rm_rf_and_remove_item_recurse_same_high_risk():
    """递归强删：rm -rf / Remove-Item -Recurse 同等，均高危。"""
    assert _ops_cmd_is_high_risk("rm -rf /tmp/foo")
    assert _ops_cmd_is_high_risk(
        'Remove-Item -Path "C:\\nginx" -Recurse -Force'
    )
    assert _ops_cmd_is_high_risk(
        "Remove-Item -Recurse -Force C:\\temp\\old"
    )
    assert _ops_plan_needs_confirm(
        ["hostname", 'Remove-Item -Path "C:\\data" -Recurse -Force']
    )


def test_stop_service_high_risk():
    assert _ops_cmd_is_high_risk("systemctl stop nginx")
    assert _ops_cmd_is_high_risk("Stop-Service nginx")
    assert _ops_plan_needs_confirm(["systemctl stop sshd"])


def test_readonly_no_confirm():
    assert not _ops_plan_needs_confirm(["hostname"])
    assert not _ops_plan_needs_confirm(["systemctl status nginx"])
    assert not _ops_cmd_is_high_risk("systemctl status nginx")


def test_reboot_still_high_risk():
    assert _ops_cmd_is_high_risk("reboot")
    assert _ops_plan_needs_confirm(["shutdown /r /t 0"])
    # Windows 重启/关机与 reboot/shutdown 同等高危
    assert _ops_cmd_is_high_risk("Restart-Computer -Force")
    assert _ops_cmd_is_high_risk("Stop-Computer -Force")
    assert _ops_plan_needs_confirm(["Restart-Computer -Force"])


def test_userdel_is_high_risk_and_needs_card():
    """删用户须弹确认卡（曾漏判为非变更，全能型直接执行）。"""
    from terminal.mobile.remote_tools import RemoteToolCall, call_needs_confirmation

    assert _ops_cmd_is_high_risk("sudo userdel -r sunsi2026")
    assert _ops_cmd_is_high_risk("userdel sunsi2026")
    assert _ops_cmd_is_high_risk("deluser --remove-home alice")
    assert _ops_cmd_is_high_risk("Remove-LocalUser -Name bob")
    assert _ops_plan_needs_confirm(["sudo userdel -r sunsi2026"])
    for cmd in (
        "sudo userdel -r sunsi2026",
        "userdel -r sunsi2026",
        "useradd -m demo",
    ):
        assert call_needs_confirmation(
            RemoteToolCall(tool="remote_run", command=cmd),
            confirm_changes=False,
        )
        assert call_needs_confirmation(
            RemoteToolCall(tool="ssh_execute", command=cmd),
            confirm_changes=False,
        )
