"""检查 ChibyTerm 是否有新版本（查询 PyPI / TestPyPI JSON API）。

不在服务进程内执行 pip；仅返回版本对比与推荐安装命令。
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_PACKAGE = "chibyterm"
_INDEX_URLS = {
    "testpypi": "https://test.pypi.org/pypi/{pkg}/json",
    "pypi": "https://pypi.org/pypi/{pkg}/json",
}


def _env_strip(*names: str) -> str:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def resolve_update_index() -> str:
    raw = _env_strip("CHIBY_UPDATE_INDEX", "CHIBYTERM_UPDATE_INDEX").lower()
    if raw in ("pypi", "pypi.org", "official"):
        return "pypi"
    return "testpypi"


def resolve_update_package() -> str:
    return _env_strip("CHIBY_UPDATE_PACKAGE", "CHIBYTERM_UPDATE_PACKAGE") or _DEFAULT_PACKAGE


def local_package_version(package: str = _DEFAULT_PACKAGE) -> str:
    name = (package or _DEFAULT_PACKAGE).strip() or _DEFAULT_PACKAGE
    try:
        if name == "chibyterm":
            from chibyterm import __version__ as v

            return str(v)
        if name == "chibycore":
            from chibycore import __version__ as v

            return str(v)
    except Exception:
        pass
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:
        return "0"


def parse_version_tuple(ver: str) -> Tuple[int, ...]:
    """宽松解析版本号为整数元组（忽略 pre/post 后缀中的非数字段）。"""
    s = str(ver or "").strip()
    if not s:
        return (0,)
    # 去掉前缀 v
    if s[:1].lower() == "v":
        s = s[1:]
    parts: List[int] = []
    for chunk in re.split(r"[.+_-]", s):
        m = re.match(r"^(\d+)", chunk)
        if m:
            parts.append(int(m.group(1)))
        elif parts:
            break
    return tuple(parts) if parts else (0,)


def is_newer(latest: str, current: str) -> bool:
    return parse_version_tuple(latest) > parse_version_tuple(current)


def build_install_cmd(*, package: str, version: str, index: str) -> str:
    pkg = (package or _DEFAULT_PACKAGE).strip() or _DEFAULT_PACKAGE
    ver = (version or "").strip()
    spec = f'{pkg}=={ver}' if ver else pkg
    if index == "pypi":
        return f'pip install -U --no-cache-dir "{spec}"'
    return (
        'pip install -U --no-cache-dir '
        '--extra-index-url https://test.pypi.org/simple/ '
        f'"{spec}"'
    )


def fetch_latest_version(package: str, index: str, *, timeout: float = 8.0) -> str:
    import httpx

    idx = index if index in _INDEX_URLS else "testpypi"
    url = _INDEX_URLS[idx].format(pkg=package)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        data = resp.json()
    ver = str((data.get("info") or {}).get("version") or "").strip()
    if not ver:
        raise ValueError("索引未返回 version")
    return ver


def check_for_update(*, timeout: float = 8.0) -> Dict[str, Any]:
    """返回升级检测结果（始终包含 current；网络失败时 ok=False）。"""
    package = resolve_update_package()
    index = resolve_update_index()
    current = local_package_version(package)
    core_ver = local_package_version("chibycore")
    out: Dict[str, Any] = {
        "ok": True,
        "package": package,
        "index": index,
        "current": current,
        "chibycore": core_ver,
        "latest": "",
        "update_available": False,
        "install_cmd": "",
        "error": None,
    }
    try:
        latest = fetch_latest_version(package, index, timeout=timeout)
        out["latest"] = latest
        out["update_available"] = is_newer(latest, current)
        out["install_cmd"] = build_install_cmd(
            package=package, version=latest, index=index
        )
    except Exception as exc:
        logger.info("update check failed (%s/%s): %s", index, package, exc)
        out["ok"] = False
        out["error"] = str(exc)[:240]
        out["install_cmd"] = build_install_cmd(
            package=package, version="", index=index
        )
    return out


def local_version_info() -> Dict[str, Any]:
    """仅本机版本（无网络），供「关于」页。"""
    return {
        "package": resolve_update_package(),
        "index": resolve_update_index(),
        "current": local_package_version(resolve_update_package()),
        "chibycore": local_package_version("chibycore"),
    }
