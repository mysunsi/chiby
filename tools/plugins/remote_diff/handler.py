"""remote_diff 薄插件 — 委托 remote_tools 执行内核。"""
from __future__ import annotations

from typing import Any, Dict

_TOOL = "remote_diff"


async def arun(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.host_plugin_delegate import arun_host_tool

    return await arun_host_tool(_TOOL, params, context)


def run(params: dict, context: dict) -> Dict[str, Any]:
    from terminal.mobile.host_plugin_delegate import sync_run_not_supported

    sync_run_not_supported(_TOOL)
    return {}


def format_result(data: dict) -> str:
    from terminal.mobile.host_plugin_delegate import format_host_result

    return format_host_result(data, tool=_TOOL)
