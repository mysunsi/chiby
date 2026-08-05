"""Linux 发行版探测映射（纯函数）。"""

from terminal.distro_profile import (
    build_distro_runtime_hint,
    map_facts_to_profile,
    needs_probe,
    parse_probe_stdout,
    profile_from_probe_stdout,
)


def test_parse_and_map_ubuntu():
    raw = """
PRETTY_NAME=Ubuntu 22.04.4 LTS
ID=ubuntu
ID_LIKE=debian
VERSION_ID=22.04
HAS_SYSTEMCTL=1
HAS_OPENRC=0
HAS_APT=1
HAS_DNF=0
HAS_YUM=0
HAS_APK=0
HAS_ZYPPER=0
HAS_PACMAN=0
UNAME_S=Linux
UNAME_M=x86_64
"""
    p = profile_from_probe_stdout(raw)
    assert p.family == "debian"
    assert p.pkg_manager == "apt"
    assert p.init_system == "systemd"
    assert "ubuntu" in (p.id or "")
    hint = build_distro_runtime_hint(p)
    assert "apt" in hint.lower()
    assert "yum" in hint or "勿用 yum" in hint


def test_map_rocky_dnf():
    facts = parse_probe_stdout(
        "PRETTY_NAME=Rocky Linux 9.3\n"
        "ID=rocky\n"
        "ID_LIKE=rhel centos fedora\n"
        "HAS_SYSTEMCTL=1\n"
        "HAS_APT=0\n"
        "HAS_DNF=1\n"
        "HAS_YUM=1\n"
        "HAS_APK=0\n"
    )
    p = map_facts_to_profile(facts)
    assert p.family == "rhel"
    assert p.pkg_manager == "dnf"
    assert "dnf" in build_distro_runtime_hint(p).lower()


def test_map_alpine_apk_openrc():
    facts = parse_probe_stdout(
        "PRETTY_NAME=Alpine Linux v3.19\n"
        "ID=alpine\n"
        "HAS_SYSTEMCTL=0\n"
        "HAS_OPENRC=1\n"
        "HAS_APT=0\n"
        "HAS_DNF=0\n"
        "HAS_APK=1\n"
    )
    p = map_facts_to_profile(facts)
    assert p.family == "alpine"
    assert p.pkg_manager == "apk"
    assert p.init_system == "openrc"
    hint = build_distro_runtime_hint(p)
    assert "apk" in hint.lower()
    assert "OpenRC" in hint or "rc-service" in hint


def test_pkg_manager_beats_misleading_id():
    # 容器里 ID=ubuntu 但只有 apk
    facts = {
        "pretty_name": "weird",
        "id": "ubuntu",
        "id_like": ["debian"],
        "has_apk": True,
        "has_apt": False,
        "has_dnf": False,
        "has_yum": False,
        "has_systemctl": False,
        "has_openrc": True,
    }
    p = map_facts_to_profile(facts)
    assert p.family == "alpine"
    assert p.pkg_manager == "apk"


def test_needs_probe_empty():
    assert needs_probe(None) is True
