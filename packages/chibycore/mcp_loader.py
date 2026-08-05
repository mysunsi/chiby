"""MCP（Model Context Protocol）toolsets：启动期预加载 + 按配置路径缓存。

可选依赖 ``pydantic_ai.mcp``；未安装或加载失败时默认降级为空列表；
设置 ``OPS_MCP_STRICT=1`` 时在严格模式下抛出异常（适合 CI）。

环境变量：
- ``OPS_MCP_CONFIG``：MCP 配置文件路径（JSON）；未设置时依次尝试
  ``{PROJECT_ROOT}/data/mcp.json``、``~/.assistant/mcp.json``。
- ``OPS_MCP_STRICT``：``1`` 为严格模式。
"""
from __future__ import annotations

import logging
import os
import warnings
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_CACHE: dict[str, List[Any]] = {}
_LOADED_ONCE = False


def _project_root() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()


def default_mcp_config_candidates() -> List[Path]:
    root = _project_root()
    home = Path.home()
    return [
        root / "data" / "mcp.json",
        home / ".assistant" / "mcp.json",
    ]


def resolve_mcp_config_path(explicit: Optional[str | Path] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    env = (os.environ.get("OPS_MCP_CONFIG") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p.resolve()
    for c in default_mcp_config_candidates():
        if c.is_file():
            return c.resolve()
    return None


def load_mcp_toolsets_cached(
    cwd: Optional[str | Path] = None,
    *,
    force_reload: bool = False,
) -> List[Any]:
    """
    解析 MCP 配置并返回 pydantic-ai toolsets 列表；结果按配置文件路径缓存。
    ``cwd`` 仅为 API 兼容保留（与 minicc 对齐）；当前实现以配置文件路径为缓存键。
    """
    global _LOADED_ONCE
    strict = os.environ.get("OPS_MCP_STRICT", "").strip() == "1"
    cfg_path = resolve_mcp_config_path()
    if not cfg_path:
        if strict:
            raise RuntimeError(
                "OPS_MCP_STRICT=1 但未找到 MCP 配置文件（设置 OPS_MCP_CONFIG 或放置 data/mcp.json）"
            )
        return []

    key = str(cfg_path.resolve())
    if not force_reload and key in _CACHE:
        return _CACHE[key]

    try:
        from pydantic_ai.mcp import load_mcp_servers
    except Exception as e:
        msg = f"MCP 依赖不可用或未安装 pydantic-ai[mcp]：{e}"
        if strict:
            raise RuntimeError(msg) from e
        warnings.warn(msg, stacklevel=2)
        _CACHE[key] = []
        _LOADED_ONCE = True
        return []

    try:
        servers = load_mcp_servers(cfg_path)
        toolsets = list(servers or [])
    except Exception as e:
        msg = f"MCP 配置加载失败 {cfg_path}: {e}"
        if strict:
            raise RuntimeError(msg) from e
        warnings.warn(msg, stacklevel=2)
        toolsets = []

    _CACHE[key] = toolsets
    _LOADED_ONCE = True
    logger.info("MCP toolsets 已加载：%s（%s 个 toolset）", cfg_path, len(toolsets))
    return toolsets


def preload_mcp_at_startup() -> List[Any]:
    """应用启动时调用一次：预热缓存；失败则返回空列表（除 STRICT）。"""
    return load_mcp_toolsets_cached(force_reload=False)


def mcp_cache_stats() -> dict[str, Any]:
    """健康检查或调试：返回缓存条目数。"""
    return {
        "cached_paths": len(_CACHE),
        "loaded_once": _LOADED_ONCE,
    }
