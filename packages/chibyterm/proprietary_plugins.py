"""可选闭源插件注册（P0-1 / P0-5 / P0-6 / P1）。

从 ``chibyterm.main`` 剥离，使开源入口对闭源包名零感知。
优先 ``chiby.plugins`` entry_points（闭源 wheel）；失败再回落 importlib。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Mapping, Optional, Set

from fastapi import FastAPI

logger = logging.getLogger(__name__)

HostProvider = Callable[[], list]
HostLookup = Callable[[str], Any]

_EP_GROUP = "chiby.plugins"


def _iter_plugin_eps():
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        if hasattr(eps, "select"):
            return list(eps.select(group=_EP_GROUP))
        return list(eps.get(_EP_GROUP, []))  # type: ignore[arg-type]
    except Exception:
        return []


def _try_entry_point_plugins(
    application: FastAPI,
    *,
    host_store: Mapping[str, Any],
    want_mobile: bool,
    want_hermes: bool,
) -> Set[str]:
    """加载匹配开关的 entry_points；返回成功注册的 ep 名集合。"""
    loaded: Set[str] = set()
    for ep in _iter_plugin_eps():
        name = (getattr(ep, "name", None) or "").strip()
        if name == "mobile_demo" and not want_mobile:
            continue
        if name == "hermes_bridge" and not want_hermes:
            continue
        if name not in ("mobile_demo", "hermes_bridge") and not (want_mobile or want_hermes):
            continue
        try:
            register = ep.load()
            register(
                application,
                host_store=host_store,
                host_provider=lambda: list(host_store.values()),
                host_lookup=lambda hid: host_store.get((hid or "").strip()),
            )
            logger.info("已通过 entry_point 加载插件: %s", name or ep)
            if name:
                loaded.add(name)
        except ImportError as exc:
            logger.info("entry_point %s 不可用（跳过）: %s", name or ep, exc)
        except Exception as exc:
            logger.warning("entry_point %s 注册失败: %s", name or ep, exc)
    return loaded


def register_optional_proprietary_plugins(
    application: FastAPI,
    *,
    host_store: Optional[Mapping[str, Any]] = None,
) -> None:
    """按开关惰性加载闭源路由；缺包时仅打日志，不抛错。"""
    from chibyterm.oss_plugin_flags import (
        hermes_bridge_routes_enabled,
        mobile_demo_enabled,
    )

    store = host_store if host_store is not None else {}
    want_mobile = mobile_demo_enabled()
    want_hermes = hermes_bridge_routes_enabled()

    loaded_eps: Set[str] = set()
    if want_hermes or want_mobile:
        loaded_eps = _try_entry_point_plugins(
            application,
            host_store=store,
            want_mobile=want_mobile,
            want_hermes=want_hermes,
        )
        if loaded_eps:
            logger.info("闭源插件 entry_points 已加载: %s", sorted(loaded_eps))

    need_hooks = (want_hermes or want_mobile) and (
        "mobile_demo" not in loaded_eps or want_hermes
    )
    if need_hooks and "mobile_demo" not in loaded_eps:
        try:
            hooks = importlib.import_module("chiby_mobile.hermes_bridge_hooks")
            hooks.install_remote_tool_registry()
        except ImportError:
            logger.info("未安装闭源桥接 hooks，Hermes Worker 使用开源默认工具契约")

    if want_hermes and "hermes_bridge" not in loaded_eps:
        try:
            hermes_ws = importlib.import_module("chiby_hermes_bridge.hermes_ws")
            hermes_audit = importlib.import_module("chiby_hermes_bridge.hermes_audit_api")
            hermes_ws.register_hermes_ws_route(application)
            hermes_audit.register_hermes_rest_routes(application)
            logger.info("已注册 Hermes 桥路由（/ws/hermes 等）")
        except ImportError as exc:
            logger.warning("Hermes 桥已启用但模块不可用: %s", exc)
    elif not want_hermes:
        logger.info("跳过 Hermes 桥路由注册（开关关闭）")

    if want_mobile and "mobile_demo" not in loaded_eps:
        try:
            demo_api = importlib.import_module("chiby_mobile.api")

            def _host_provider():
                return list(store.values())

            def _host_lookup(host_id: str):
                return store.get((host_id or "").strip())

            demo_api.register_mobile_demo_routes(
                application,
                host_provider=_host_provider,
                host_lookup=_host_lookup,
            )
            logger.info("已注册闭源演示路由")
        except ImportError as exc:
            logger.warning("闭源演示已启用但模块不可用: %s", exc)
    elif not want_mobile:
        logger.info("跳过闭源演示路由注册（开关关闭）")
