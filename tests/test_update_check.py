"""chibyterm.update_check 版本比较与安装命令。"""
from __future__ import annotations


def test_parse_and_compare():
    from chibyterm.update_check import is_newer, parse_version_tuple

    assert parse_version_tuple("0.1.1") == (0, 1, 1)
    assert parse_version_tuple("v0.1.0") == (0, 1, 0)
    assert is_newer("0.1.1", "0.1.0")
    assert not is_newer("0.1.0", "0.1.1")
    assert not is_newer("0.1.1", "0.1.1")


def test_build_install_cmd_testpypi():
    from chibyterm.update_check import build_install_cmd

    cmd = build_install_cmd(package="chibyterm", version="0.1.1", index="testpypi")
    assert "test.pypi.org" in cmd
    assert "chibyterm==0.1.1" in cmd
    assert "--no-cache-dir" in cmd


def test_build_install_cmd_pypi():
    from chibyterm.update_check import build_install_cmd

    cmd = build_install_cmd(package="chibyterm", version="0.2.0", index="pypi")
    assert "test.pypi.org" not in cmd
    assert 'chibyterm==0.2.0' in cmd
