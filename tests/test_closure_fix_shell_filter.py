"""闭环修复：Linux 目标不得接受 PowerShell / 跨族装包候选。"""

import json

from chibycore.closure_llm_fix import (
    build_fix_user_message,
    filter_fix_commands_for_shell,
    looks_like_cross_family_pkg_command,
    looks_like_powershell_command,
)
from chibycore.closure_service import ClosurePayload
from chibycore.executor_contract import RiskLevel


def test_looks_like_powershell_detects_test_path_remove_item():
    ps = (
        "if (Test-Path -Path '.\\a.dat') { Remove-Item -Path '.\\a.dat' -Force } "
        "else { Write-Output 'missing' }"
    )
    assert looks_like_powershell_command(ps)
    assert not looks_like_powershell_command("rm -f a.dat")
    assert not looks_like_powershell_command(
        "if [ -e a.dat ]; then rm -f a.dat; else echo missing; fi"
    )


def test_filter_drops_powershell_on_unix():
    cmds = [
        "rm -f a.dat",
        "if (Test-Path -Path '.\\a.dat') { Remove-Item -Path '.\\a.dat' -Force }",
        "Write-Output 'x'",
    ]
    out = filter_fix_commands_for_shell(cmds, "unix")
    assert out == ["rm -f a.dat"]


def test_filter_keeps_powershell_on_powershell_profile():
    cmds = [
        "Remove-Item -Path '.\\a.dat' -Force",
        "rm -f a.dat",
    ]
    out = filter_fix_commands_for_shell(cmds, "powershell")
    assert "Remove-Item -Path '.\\a.dat' -Force" in out


def test_cross_family_pkg_detects_yum_on_debian():
    assert looks_like_cross_family_pkg_command("sudo yum install -y nginx", "debian")
    assert looks_like_cross_family_pkg_command("dnf install curl", "debian")
    assert looks_like_cross_family_pkg_command("apk add curl", "debian")
    assert not looks_like_cross_family_pkg_command("sudo apt-get install -y nginx", "debian")
    assert not looks_like_cross_family_pkg_command("echo prefer-yum-on-rhel", "debian")


def test_filter_drops_cross_family_pkg_on_debian():
    cmds = [
        "sudo apt-get install -y nginx",
        "yum install -y nginx",
        "apk add nginx",
        "systemctl status nginx",
    ]
    out = filter_fix_commands_for_shell(
        cmds, "unix", distro_family="debian", pkg_manager="apt"
    )
    assert out == ["sudo apt-get install -y nginx", "systemctl status nginx"]


def test_filter_drops_apt_on_rhel():
    out = filter_fix_commands_for_shell(
        ["apt install nginx", "dnf install -y nginx"],
        "unix",
        distro_family="rhel",
        pkg_manager="dnf",
    )
    assert out == ["dnf install -y nginx"]


def test_filter_skips_distro_when_generic_or_missing():
    cmds = ["yum install -y x", "apt install x"]
    assert filter_fix_commands_for_shell(cmds, "unix") == cmds
    assert (
        filter_fix_commands_for_shell(cmds, "unix", distro_family="linux_generic")
        == cmds
    )


def test_build_fix_user_message_includes_distro_family():
    cp = ClosurePayload(
        trace_id="t1",
        raw_command="apt install x",
        effective_command="apt install x",
        transport="ssh",
        risk_level=RiskLevel.LOW,
        exit_code=1,
        stdout="",
        stderr="E: Unable to locate package",
    )
    raw = build_fix_user_message(
        [cp], "unix", distro_family="debian", pkg_manager="apt"
    )
    obj = json.loads(raw)
    assert obj["distro_family"] == "debian"
    assert obj["pkg_manager"] == "apt"
    assert "distro_family=debian" in obj["instruction"]
