"""remote_rollback 薄插件 — 委托 remote_tools（内核侧等同 restore）。"""
from __future__ import annotations

from typing import Any, Dict

_TOOL = "remote_rollback"


async def arun(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.host_plugin_delegate import arun_host_tool

    # 执行名与解析归一一致：走 restore 命令编译
    return await arun_host_tool("remote_restore", params, context)


def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.host_plugin_delegate import sync_run_not_supported

    sync_run_not_supported(_TOOL)
    return {}


def format_result(data: dict) -> str:
    from terminal.mobile.host_plugin_delegate import format_host_result

    return format_host_result(data, tool=_TOOL)
