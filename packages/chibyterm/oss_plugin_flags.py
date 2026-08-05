"""开源终端插件开关（P0-1 / P0-6）。

默认关闭掌上 Demo 与 Hermes 桥路由注册，使 ``uvicorn terminal.main:app``
在零闭源意图下不 ``import chibyterm.mobile`` / 不强制拉起 ACP。

优先级（高 → 低）：
1. 环境变量强制（``OPS_MOBILE_DEMO`` / ``MOBILE_DEMO_ENABLED`` 等）
2. ``data/hermes_bridge.yaml`` 中对应字段
3. 默认 ``False``

环境变量取值：``1|true|yes|on`` → 开；``0|false|no|off`` → 关。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

from chibycore.repo_root import find_repo_root

_PROJECT_ROOT = find_repo_root()


def _parse_bool_env(*names: str) -> Optional[bool]:
    for name in names:
        raw = (os.environ.get(name) or "").strip().lower()
        if not raw:
            continue
        if raw in ("1", "true", "yes", "on"):
            return True
        if raw in ("0", "false", "no", "off"):
            return False
    return None


def _load_bridge_yaml() -> Mapping:
    path = _PROJECT_ROOT / "data" / "hermes_bridge.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, Mapping) else {}
    except Exception as exc:
        logger.debug("读取 hermes_bridge.yaml 失败（插件开关降级默认）: %s", exc)
        return {}


def mobile_demo_enabled() -> bool:
    """是否注册掌上 AI 机房演示路由。默认 False。"""
    forced = _parse_bool_env(
        "OPS_MOBILE_DEMO",
        "OPS_MOBILE_DEMO_ENABLED",
        "MOBILE_DEMO_ENABLED",
    )
    if forced is not None:
        return forced
    raw = _load_bridge_yaml()
    md = raw.get("mobile_demo") or {}
    if isinstance(md, Mapping) and "enabled" in md:
        return bool(md.get("enabled"))
    return False


def hermes_bridge_routes_enabled() -> bool:
    """是否注册 ``/ws/hermes`` 与 Hermes REST。默认 False。

    与 ``HermesBridgeConfig.enabled``（子进程是否可用）对齐，但可被环境变量单独强制。
    """
    forced = _parse_bool_env(
        "OPS_HERMES_BRIDGE",
        "OPS_HERMES_BRIDGE_ENABLED",
        "HERMES_BRIDGE_ENABLED",
    )
    if forced is not None:
        return forced
    raw = _load_bridge_yaml()
    if "enabled" in raw:
        return bool(raw.get("enabled"))
    return False
