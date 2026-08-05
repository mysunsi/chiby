"""测试收集前注册包名别名（P0-7 / P1 过渡）。

- ``terminal`` → ``chibyterm``
- ``terminal.mobile`` / ``chibyterm.mobile`` → ``chiby_mobile``
- ``terminal.hermes_bridge`` / ``chibyterm.hermes_bridge`` → ``chiby_hermes_bridge``
- ``terminal.hermes_ws`` / ``terminal.hermes_audit_api`` → 闭源包内同名模块
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_EXTRA_PATHS = [
    _ROOT / "packages",
    _ROOT / "proprietary" / "chiby_mobile" / "src",
    _ROOT / "proprietary" / "chiby_hermes_bridge" / "src",
]
for _p in _EXTRA_PATHS:
    if _p.is_dir():
        s = str(_p)
        if s not in sys.path:
            sys.path.insert(0, s)


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, real_name: str) -> None:
        self.real_name = real_name

    def create_module(self, spec):  # noqa: ANN001
        mod = importlib.import_module(self.real_name)
        sys.modules[spec.name] = mod
        return mod

    def exec_module(self, module) -> None:  # noqa: ANN001
        return None


_ALIAS_MAP = {
    "terminal": "chibyterm",
    "ops_terminal": "chibyterm",
    "ops_core": "chibycore",
    "terminal.mobile": "chiby_mobile",
    "chibyterm.mobile": "chiby_mobile",
    "ops_terminal.mobile": "chiby_mobile",
    "terminal.hermes_bridge": "chiby_hermes_bridge",
    "chibyterm.hermes_bridge": "chiby_hermes_bridge",
    "ops_terminal.hermes_bridge": "chiby_hermes_bridge",
    "terminal.hermes_ws": "chiby_hermes_bridge.hermes_ws",
    "chibyterm.hermes_ws": "chiby_hermes_bridge.hermes_ws",
    "ops_terminal.hermes_ws": "chiby_hermes_bridge.hermes_ws",
    "terminal.hermes_audit_api": "chiby_hermes_bridge.hermes_audit_api",
    "chibyterm.hermes_audit_api": "chiby_hermes_bridge.hermes_audit_api",
    "ops_terminal.hermes_audit_api": "chiby_hermes_bridge.hermes_audit_api",
}


def _resolve_alias(fullname: str) -> str | None:
    if fullname in _ALIAS_MAP:
        return _ALIAS_MAP[fullname]
    for prefix, real in (
        ("terminal.mobile.", "chiby_mobile."),
        ("chibyterm.mobile.", "chiby_mobile."),
        ("ops_terminal.mobile.", "chiby_mobile."),
        ("terminal.hermes_bridge.", "chiby_hermes_bridge."),
        ("chibyterm.hermes_bridge.", "chiby_hermes_bridge."),
        ("ops_terminal.hermes_bridge.", "chiby_hermes_bridge."),
        ("terminal.", "chibyterm."),
        ("ops_terminal.", "chibyterm."),
        ("ops_core.", "chibycore."),
    ):
        if fullname.startswith(prefix):
            return real + fullname[len(prefix) :]
    return None


class _TerminalAliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):  # noqa: ANN001
        alt = _resolve_alias(fullname)
        if alt is None:
            return None
        if alt in sys.modules:
            return importlib.util.spec_from_loader(
                fullname,
                _AliasLoader(alt),
                is_package=hasattr(sys.modules[alt], "__path__"),
            )
        try:
            real_spec = importlib.util.find_spec(alt)
        except (ImportError, ModuleNotFoundError, ValueError, KeyError):
            return None
        if real_spec is None:
            return None
        is_pkg = real_spec.submodule_search_locations is not None
        return importlib.util.spec_from_loader(fullname, _AliasLoader(alt), is_package=is_pkg)


def _install_terminal_alias() -> None:
    if any(isinstance(x, _TerminalAliasFinder) for x in sys.meta_path):
        return
    sys.meta_path.insert(0, _TerminalAliasFinder())


_install_terminal_alias()


# ── 闭源用例自动标记（pytest -m "not proprietary"）──────────────────────────
# 凡静态 import 闭源包（或别名）的测试文件，自动打 proprietary，避免漏标导致 OSS CI 红。
import ast

import pytest

_PROP_IMPORT_ROOTS = (
    "chiby_mobile",
    "chiby_hermes_bridge",
    "terminal.mobile",
    "terminal.hermes_bridge",
    "terminal.hermes_ws",
    "terminal.hermes_audit_api",
    "chibyterm.mobile",
    "chibyterm.hermes_bridge",
    "ops_terminal.mobile",
    "ops_terminal.hermes_bridge",
)
_PROP_NAME_PREFIXES = (
    "test_mobile_",
    "test_hermes_",
    "test_omnipotent_",
    "test_a2_",
    "test_feishu_",
)


def _module_imports_proprietary(path: Path) -> bool:
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError, UnicodeError):
        return False
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if any(name == r or name.startswith(r + ".") for r in _PROP_IMPORT_ROOTS):
                return True
    return False


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    cache: dict[str, bool] = {}
    mark = pytest.mark.proprietary
    for item in items:
        try:
            path = Path(str(item.path))
        except AttributeError:
            path = Path(str(item.fspath))
        key = str(path.resolve())
        if key not in cache:
            cache[key] = path.name.startswith(_PROP_NAME_PREFIXES) or _module_imports_proprietary(
                path
            )
        if cache[key]:
            item.add_marker(mark)
