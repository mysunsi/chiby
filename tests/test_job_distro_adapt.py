"""P2：多主机扇出按 distro 改写装包/服务命令。"""

from __future__ import annotations

import pytest

from terminal.distro_profile import DistroProfile
from terminal.mobile.headless_exec import FakeHeadlessExecutor
from terminal.mobile.job_distro_adapt import adapt_command_for_distro, adapt_commands_for_distro
from terminal.mobile.job_orchestrator import JobOrchestrator
from terminal.mobile.models import HostSummary


def _dp(**kwargs) -> DistroProfile:
    base = dict(
        probed_at="2099-01-01T00:00:00+00:00",
        probe_source="manual",
    )
    base.update(kwargs)
    return DistroProfile(**base)


def test_apt_install_rewritten_to_dnf_on_rhel():
    dp = _dp(family="rhel", pkg_manager="dnf", pretty_name="Rocky 9")
    assert (
        adapt_command_for_distro("sudo apt-get install -y nginx", dp)
        == "sudo dnf install -y nginx"
    )


def test_yum_install_rewritten_to_apk_on_alpine():
    dp = _dp(family="alpine", pkg_manager="apk", init_system="openrc")
    assert adapt_command_for_distro("yum install -y curl", dp) == "apk add curl"


def test_dnf_unchanged_on_rhel():
    dp = _dp(family="rhel", pkg_manager="dnf")
    assert adapt_command_for_distro("dnf install -y curl", dp) == "dnf install -y curl"


def test_systemctl_to_rc_service_on_alpine():
    dp = _dp(family="alpine", pkg_manager="apk", init_system="openrc")
    assert (
        adapt_command_for_distro("systemctl is-active nginx", dp)
        == "rc-service nginx status"
    )


def test_no_adapt_without_profile():
    assert adapt_command_for_distro("apt-get install -y x", None) == "apt-get install -y x"
    assert (
        adapt_command_for_distro(
            "apt-get install -y x",
            _dp(family="linux_generic", pkg_manager="unknown"),
        )
        == "apt-get install -y x"
    )


def test_adapt_commands_list():
    dp = _dp(family="debian", pkg_manager="apt")
    out = adapt_commands_for_distro(
        ["dnf install -y nginx", "systemctl is-active nginx"],
        dp,
    )
    assert out[0] == "apt-get install -y nginx"
    assert out[1] == "systemctl is-active nginx"  # debian 保留 systemctl


@pytest.mark.asyncio
async def test_job_fanout_per_host_commands():
    hosts = [
        HostSummary(
            id="u1",
            name="ubuntu",
            host="1.1.1.1",
            distro_profile=_dp(family="debian", pkg_manager="apt").model_dump(),
        ),
        HostSummary(
            id="r1",
            name="rocky",
            host="2.2.2.2",
            distro_profile=_dp(family="rhel", pkg_manager="dnf").model_dump(),
        ),
        HostSummary(
            id="a1",
            name="alpine",
            host="3.3.3.3",
            distro_profile=_dp(
                family="alpine", pkg_manager="apk", init_system="openrc"
            ).model_dump(),
        ),
    ]
    orch = JobOrchestrator(FakeHeadlessExecutor(), default_timeout_sec=30.0)
    run = await orch.run(
        name="装 curl",
        host_ids=["u1", "r1", "a1"],
        commands=["sudo apt-get install -y curl"],
        hosts=hosts,
        max_parallel=3,
    )
    by_id = {t.host_id: t for t in run.tasks}
    assert by_id["u1"].commands == ["sudo apt-get install -y curl"]
    assert by_id["r1"].commands == ["sudo dnf install -y curl"]
    assert by_id["a1"].commands == ["sudo apk add curl"]


@pytest.mark.asyncio
async def test_job_fanout_nginx_check_alpine_uses_rc_service():
    hosts = [
        HostSummary(
            id="a1",
            name="alpine",
            host="3.3.3.3",
            distro_profile=_dp(
                family="alpine", pkg_manager="apk", init_system="openrc"
            ).model_dump(),
        ),
        HostSummary(
            id="u1",
            name="ubuntu",
            host="1.1.1.1",
            distro_profile=_dp(family="debian", pkg_manager="apt").model_dump(),
        ),
    ]
    orch = JobOrchestrator(FakeHeadlessExecutor(), default_timeout_sec=30.0)
    run = await orch.run(
        name="检测 nginx",
        host_ids=["a1", "u1"],
        commands=["systemctl is-active nginx"],
        hosts=hosts,
        max_parallel=2,
    )
    by_id = {t.host_id: t for t in run.tasks}
    assert by_id["a1"].commands == ["rc-service nginx status"]
    assert by_id["u1"].commands == ["systemctl is-active nginx"]
