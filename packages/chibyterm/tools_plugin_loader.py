"""工具目录插件加载器：扫描 tools/plugins/*/manifest.yaml + handler.py。

Phase 1：local_readonly / local_write
Phase 3：host_readonly（薄 handler → remote_tools 内核）
详见 docs/tool-plugin-architecture.md · docs/host-plugin-contract.md
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger(__name__)

_FORBIDDEN_PARAM_SUBSTR = ("password", "secret", "token", "api_key")
_LOCAL_TYPES = frozenset({"local_readonly", "local_write"})
_HOST_TYPES = frozenset(
    {"host_readonly", "host_write", "host_command"}
)  # Phase 3–5；host_shell 可与 host_command 同义扩展
_ALLOWED_TYPES = _LOCAL_TYPES | _HOST_TYPES

_lock = threading.RLock()
_registry: Optional["PluginRegistry"] = None


def _repo_root() -> Path:
    from chibycore.repo_root import find_repo_root

    return find_repo_root()


def default_plugins_dir() -> Path:
    env = (os.environ.get("OPS_TOOL_PLUGINS_DIR") or "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return (_repo_root() / "tools" / "plugins").resolve()


def plugins_enabled() -> bool:
    v = (os.environ.get("OPS_TOOL_PLUGINS") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


@dataclass
class PluginTool:
    name: str
    manifest: Dict[str, Any]
    module: Any
    path: Path

    @property
    def tool_type(self) -> str:
        return str(self.manifest.get("type") or "local_readonly").strip()

    @property
    def host_required(self) -> bool:
        return bool(self.manifest.get("host_required"))

    @property
    def read_only(self) -> bool:
        sec = self.manifest.get("security") or {}
        if isinstance(sec, Mapping) and "read_only" in sec:
            return bool(sec.get("read_only"))
        return self.tool_type == "local_readonly"

    @property
    def needs_confirmation(self) -> bool:
        sec = self.manifest.get("security") or {}
        if isinstance(sec, Mapping) and "needs_confirmation" in sec:
            return bool(sec.get("needs_confirmation"))
        if self.tool_type == "host_write":
            return True
        return self.tool_type == "local_write"

    @property
    def is_mutate(self) -> bool:
        """写入/变更类：orchestrator 可据此识别（manifest.security.is_mutate 或 type）。"""
        sec = self.manifest.get("security") or {}
        if isinstance(sec, Mapping) and "is_mutate" in sec:
            return bool(sec.get("is_mutate"))
        if self.tool_type in ("host_write", "local_write"):
            return True
        return bool(self.needs_confirmation and not self.read_only)

    @property
    def confirm_fields(self) -> List[str]:
        """确认卡建议展示字段（文档/市场用；pending 仍由 remote_tools 组装）。"""
        raw = self.manifest.get("confirm_fields")
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x or "").strip()]
        return []

    @property
    def entry(self) -> str:
        ex = self.manifest.get("executor") or {}
        if isinstance(ex, Mapping):
            return str(ex.get("entry") or "run").strip() or "run"
        return "run"

    @property
    def parameters(self) -> List[Dict[str, Any]]:
        raw = self.manifest.get("parameters") or []
        return [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []

    @property
    def usage_example(self) -> str:
        return str(self.manifest.get("usage_example") or "").strip()

    @property
    def description(self) -> str:
        return str(self.manifest.get("description") or "").strip()

    def extract_params(self, raw: Mapping[str, Any]) -> Dict[str, Any]:
        """按 manifest.parameters 抽参；无定义时拷贝非保留键。"""
        reserved = {"tool", "password", "secret", "token"}
        if not self.host_required:
            reserved |= {"host", "hosts", "timeout_sec"}
        if not self.parameters:
            return {
                str(k): v
                for k, v in (raw or {}).items()
                if str(k) not in reserved and not str(k).startswith("_")
            }
        out: Dict[str, Any] = {}
        for p in self.parameters:
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            if name in raw:
                out[name] = raw[name]
            elif name == "text" and raw.get("q") is not None:
                out[name] = raw.get("q")
        return out

    def validate_required(self, params: Mapping[str, Any]) -> Optional[str]:
        for p in self.parameters:
            if not p.get("required"):
                continue
            name = str(p.get("name") or "").strip()
            if not name:
                continue
            val = params.get(name)
            if val is None or (isinstance(val, str) and not str(val).strip()):
                return f"缺少必填参数 {name}"
        return None


@dataclass
class PluginRegistry:
    tools: Dict[str, PluginTool] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)

    def ids(self) -> Set[str]:
        return set(self.tools.keys())

    def get(self, name: str) -> Optional[PluginTool]:
        return self.tools.get((name or "").strip())

    def as_catalog_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for t in sorted(self.tools.values(), key=lambda x: x.name):
            m = t.manifest
            sec = m.get("security") if isinstance(m.get("security"), Mapping) else {}
            deps = _normalize_dependencies(m.get("dependencies"))
            rows.append(
                {
                    "id": t.name,
                    "title": str(m.get("title") or t.name),
                    "category": str(m.get("category") or "plugin"),
                    "skill_pack": str(m.get("skill_pack") or m.get("category") or "plugin"),
                    "scope": "host" if t.host_required else "local",
                    "readonly": t.read_only,
                    "status": "loaded",
                    "loaded": True,
                    "summary": t.description or f"插件工具 {t.name}",
                    "path": f"tools/plugins/{t.name}/",
                    "version": str(m.get("version") or "0.0.0"),
                    "author": str(m.get("author") or ""),
                    "source": "plugins",
                    "type": t.tool_type,
                    "host_required": t.host_required,
                    "needs_confirmation": t.needs_confirmation,
                    "confirm_mode": str((sec or {}).get("confirm_mode") or ""),
                    "is_mutate": t.is_mutate,
                    "confirm_fields": list(t.confirm_fields),
                    "dependencies": deps,
                }
            )
        return rows


def _normalize_dependencies(raw: Any) -> List[Dict[str, Any]]:
    """manifest.dependencies → [{id|name, kind, optional}, ...]。"""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append({"id": s, "kind": "tool", "optional": False})
            continue
        if not isinstance(item, Mapping):
            continue
        tid = str(item.get("id") or item.get("name") or "").strip()
        if not tid:
            continue
        kind = str(item.get("kind") or "tool").strip().lower() or "tool"
        out.append(
            {
                "id": tid,
                "kind": kind,
                "optional": bool(item.get("optional")),
            }
        )
    return out


def get_plugin_detail(tool_id: str) -> Optional[Dict[str, Any]]:
    """单工具详情（已加载优先；否则扫目录未批准条目）。"""
    tid = (tool_id or "").strip()
    if not tid:
        return None
    reg = get_registry()
    p = reg.get(tid)
    if p is not None:
        for row in reg.as_catalog_rows():
            if row.get("id") == tid:
                row = dict(row)
                row["parameters"] = list(p.parameters)
                row["usage_example"] = p.usage_example
                return row
    for row in list_plugin_manifests_for_catalog(include_unapproved=True):
        if row.get("id") == tid:
            return dict(row)
    return None


def _load_yaml(path: Path) -> Optional[Dict[str, Any]]:
    try:
        import yaml
    except ImportError:
        logger.warning("PyYAML 不可用，无法加载插件 manifest: %s", path)
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as e:
        logger.warning("读取 manifest 失败 %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def _param_names_forbidden(params: Sequence[Mapping[str, Any]]) -> Optional[str]:
    for p in params:
        name = str(p.get("name") or "").strip().lower()
        for bad in _FORBIDDEN_PARAM_SUBSTR:
            if bad in name:
                return f"参数名禁止包含 {bad}: {name}"
    return None


def _import_handler(tool_name: str, handler_path: Path) -> Optional[Any]:
    try:
        resolved = handler_path.resolve()
        if resolved.name != "handler.py":
            logger.warning("拒绝非 handler.py: %s", resolved)
            return None
        if resolved.parent.name != tool_name:
            logger.warning("handler 目录名与 tool 名不一致: %s", resolved)
            return None
        spec = importlib.util.spec_from_file_location(
            f"chiby_tool_plugin_{tool_name}",
            resolved,
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        logger.warning("导入 handler 失败 %s: %s", handler_path, e)
        return None


# 已迁至 tools/plugins/ 的本地知识/文档/示例工具（允许同名插件注册）
MIGRATED_LOCAL_PLUGIN_TOOLS: frozenset[str] = frozenset(
    {
        "example_echo",
        "host_list",
        "kb_search",
        "kb_get",
        "kb_ingest",
        "doc_search",
        "doc_get",
        "search_knowledge",
        "get_content",
    }
)

# Phase 3：已迁主机只读（允许同名插件；执行仍委托 remote_tools 内核）
MIGRATED_HOST_PLUGIN_TOOLS: frozenset[str] = frozenset(
    {
        "remote_read_file",
        "remote_list_dir",
        "remote_grep",
        "remote_search",
        "remote_diff",
        "remote_logs",
        "remote_backup",
        "remote_syntax_check",
        "remote_write_file",
        "remote_mkdir",
        "remote_remove",
        "remote_restore",
        "remote_rollback",
        "remote_run",
        "ssh_execute",
        "winrm_execute",
    }
)


def _builtin_reserved_names() -> Set[str]:
    """不可被插件覆盖的名字：尚未迁出的硬编码工具。"""
    try:
        from chibyterm.models.tools import DEFAULT_ALLOWED_TOOLS

        return (
            set(DEFAULT_ALLOWED_TOOLS)
            - set(MIGRATED_LOCAL_PLUGIN_TOOLS)
            - set(MIGRATED_HOST_PLUGIN_TOOLS)
        )
    except Exception:
        return {
            "ssh_execute",
            "winrm_execute",
            "ssh_batch",
            "winrm_batch",
            "remote_run",
            "remote_list_dir",
            "remote_write_file",
        }


def discover_plugins(
    *,
    root: Optional[Path] = None,
    force: bool = False,
) -> PluginRegistry:
    """扫描并加载 approved 本地插件。"""
    global _registry
    with _lock:
        if _registry is not None and not force:
            return _registry
        reg = PluginRegistry()
        if not plugins_enabled():
            logger.info("OPS_TOOL_PLUGINS 已关闭，跳过插件发现")
            _registry = reg
            return reg

        plugins_root = (root or default_plugins_dir()).resolve()
        if not plugins_root.is_dir():
            logger.debug("插件目录不存在: %s", plugins_root)
            _registry = reg
            return reg

        reserved = _builtin_reserved_names()
        for child in sorted(plugins_root.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            manifest_path = child / "manifest.yaml"
            if not manifest_path.is_file():
                manifest_path = child / "manifest.yml"
            handler_path = child / "handler.py"
            if not manifest_path.is_file() or not handler_path.is_file():
                continue

            manifest = _load_yaml(manifest_path)
            if not manifest:
                reg.errors.append(f"{child.name}: invalid manifest")
                continue

            name = str(manifest.get("name") or "").strip()
            if not name:
                reg.errors.append(f"{child.name}: missing name")
                continue
            if name != child.name:
                msg = f"{child.name}: name={name} 与目录名不一致"
                logger.warning(msg)
                reg.errors.append(msg)
                continue

            status = str(manifest.get("status") or "").strip().lower()
            if status != "approved":
                logger.debug("跳过未批准插件 %s status=%s", name, status)
                continue

            tool_type = str(manifest.get("type") or "").strip()
            host_req = bool(manifest.get("host_required"))
            if tool_type not in _ALLOWED_TYPES:
                msg = f"{name}: 不支持 type={tool_type}（允许 {_ALLOWED_TYPES}）"
                logger.warning(msg)
                reg.errors.append(msg)
                continue
            if host_req and tool_type not in _HOST_TYPES:
                msg = f"{name}: host_required=true 需要 type 为 host_*（当前 {tool_type}）"
                logger.warning(msg)
                reg.errors.append(msg)
                continue
            if not host_req and tool_type in _HOST_TYPES:
                msg = f"{name}: type={tool_type} 要求 host_required=true"
                logger.warning(msg)
                reg.errors.append(msg)
                continue
            if host_req:
                params_chk = manifest.get("parameters") or []
                param_names = {
                    str(p.get("name") or "").strip()
                    for p in params_chk
                    if isinstance(p, Mapping)
                }
                cat = str(manifest.get("category") or "").strip()
                if "host" not in param_names and cat != "remote_batch":
                    msg = f"{name}: 主机插件缺少 parameters.host"
                    logger.warning(msg)
                    reg.errors.append(msg)
                    continue

            params = manifest.get("parameters") or []
            if isinstance(params, list):
                bad = _param_names_forbidden(
                    [p for p in params if isinstance(p, Mapping)]
                )
                if bad:
                    logger.warning("%s: %s", name, bad)
                    reg.errors.append(f"{name}: {bad}")
                    continue

            if name in reserved:
                msg = f"{name}: 与内置工具冲突，builtin 优先，跳过插件"
                logger.warning(msg)
                reg.errors.append(msg)
                continue

            mod = _import_handler(name, handler_path)
            if mod is None:
                reg.errors.append(f"{name}: handler import failed")
                continue

            entry = "run"
            ex = manifest.get("executor") or {}
            if isinstance(ex, Mapping):
                entry = str(ex.get("entry") or "run").strip() or "run"
            if not hasattr(mod, entry) and not hasattr(mod, "arun"):
                reg.errors.append(f"{name}: missing {entry}/arun")
                continue

            reg.tools[name] = PluginTool(
                name=name,
                manifest=manifest,
                module=mod,
                path=child,
            )
            logger.info("已加载工具插件: %s (%s)", name, tool_type)

        _registry = reg
        return reg


def reset_registry() -> None:
    """测试用：清空缓存。"""
    global _registry
    with _lock:
        _registry = None


def get_registry(*, force: bool = False) -> PluginRegistry:
    return discover_plugins(force=force)


def plugin_tool_ids() -> Set[str]:
    return get_registry().ids()


def is_plugin_tool(tool: str) -> bool:
    return (tool or "").strip() in get_registry().tools


def plugin_needs_confirmation(tool: str) -> Optional[bool]:
    p = get_registry().get(tool)
    if p is None:
        return None
    sec = p.manifest.get("security") or {}
    if isinstance(sec, Mapping):
        mode = str(sec.get("confirm_mode") or "").strip().lower()
        # 命令面：按 command 内容判定（与迁前 remote_run/ssh_execute 一致）
        if mode in ("command", "command_content", "defer"):
            return None
    return p.needs_confirmation


def plugin_is_readonly(tool: str) -> bool:
    p = get_registry().get(tool)
    if p is None:
        return False
    return bool(p.read_only)


def plugin_is_local_no_host(tool: str) -> bool:
    p = get_registry().get(tool)
    if p is None:
        return False
    return not p.host_required


def plugin_host_required(tool: str) -> bool:
    """主机类插件（需 resolve_host / executor）。与 plugin_is_local_no_host 互斥。"""
    p = get_registry().get(tool)
    if p is None:
        return False
    return bool(p.host_required)


def plugin_is_mutate(tool: str) -> bool:
    p = get_registry().get(tool)
    if p is None:
        return False
    return bool(p.is_mutate)


def merge_plugin_tools(
    allowed: Sequence[str],
    *,
    auto_merge: bool = True,
) -> List[str]:
    """白名单 ∪ 已加载插件 id（去重保序）。"""
    out: List[str] = []
    seen: Set[str] = set()
    for x in allowed or []:
        s = str(x or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    if auto_merge and plugins_enabled():
        for pid in sorted(plugin_tool_ids()):
            if pid not in seen:
                seen.add(pid)
                out.append(pid)
    return out


def effective_allowed_tools(
    base: Optional[Sequence[str]] = None,
    *,
    auto_merge: bool = True,
) -> List[str]:
    from chibyterm.models.tools import DEFAULT_ALLOWED_TOOLS

    seed = list(base) if base is not None else list(DEFAULT_ALLOWED_TOOLS)
    return merge_plugin_tools(seed, auto_merge=auto_merge)


def format_plugin_preamble(allowed: Optional[Sequence[str]] = None) -> str:
    allow = set(allowed or []) if allowed is not None else None
    lines: List[str] = []
    for name, plugin in sorted(get_registry().tools.items()):
        if allow is not None and name not in allow:
            continue
        desc = plugin.description or name
        ex = plugin.usage_example or f'{{"tool":"{name}"}}'
        kind = "主机" if plugin.host_required else "本地 · 无 host"
        lines.append(f"- **插件工具（{kind}）**：`{name}` — {desc}\n")
        lines.append("```\n")
        lines.append(ex.rstrip() + "\n")
        lines.append("```\n")
    return "".join(lines)


def plugin_shell_text(tool: str, raw: Optional[Mapping[str, Any]] = None) -> str:
    p = get_registry().get(tool)
    raw = raw or {}
    if p is None:
        return tool
    # 命令面：优先 command/script，避免 host 抢先变成「tool · host_id」导致跳过确认
    for key in ("command", "script", "path", "pattern", "query", "q"):
        s = str(raw.get(key) or "").strip()
        if s:
            return f"{tool} · {s[:120]}"
    params = p.extract_params(raw)
    # 取第一个有值的参数做预览（跳过纯定位字段 host/hosts）
    skip = {"host", "hosts", "timeout", "timeout_sec", "stream", "streaming"}
    for key, val in params.items():
        if str(key or "").strip().lower() in skip:
            continue
        s = str(val or "").strip()
        if s:
            return f"{tool} · {s[:120]}"
    return tool


async def execute_plugin(
    tool: str,
    *,
    raw: Optional[Mapping[str, Any]] = None,
    agent_mode: str = "omnipotent",
    context_extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """执行插件；返回 {ok, stdout, error, error_code, data, duration_ms}。

    ``context_extra`` 可注入运行时能力（如 ``list_visible_hosts``），不含凭据。
    """
    import time

    p = get_registry().get(tool)
    if p is None:
        return {
            "ok": False,
            "error_code": "plugin_not_found",
            "error": f"插件未加载: {tool}",
            "data": {},
            "stdout": "",
        }
    raw = dict(raw or {})
    params = p.extract_params(raw)
    missing = p.validate_required(params)
    if missing:
        return {
            "ok": False,
            "error_code": "fields_required",
            "error": missing,
            "data": {"ok": False, "error": missing, "error_code": "fields_required"},
            "stdout": missing,
        }

    context: Dict[str, Any] = {"agent_mode": agent_mode, "raw": raw}
    if context_extra:
        for k, v in context_extra.items():
            if k in ("password", "secret", "token", "api_key"):
                continue
            context[k] = v
    t0 = time.perf_counter()
    try:
        arun = getattr(p.module, "arun", None)
        run_fn = getattr(p.module, p.entry, None)
        if arun is not None and callable(arun):
            result = await arun(params, context)
        elif run_fn is not None and callable(run_fn):
            result = await asyncio.to_thread(run_fn, params, context)
        else:
            return {
                "ok": False,
                "error_code": "plugin_entry_missing",
                "error": f"缺少入口 {p.entry}",
                "data": {},
                "stdout": "",
            }
    except Exception as e:
        logger.exception("插件执行失败 %s", tool)
        return {
            "ok": False,
            "error_code": "plugin_error",
            "error": str(e),
            "data": {},
            "stdout": str(e),
            "duration_ms": int((time.perf_counter() - t0) * 1000),
        }

    if not isinstance(result, dict):
        result = {"ok": True, "result": result}
    ok = bool(result.get("ok"))
    fmt = getattr(p.module, "format_result", None)
    if fmt is not None and callable(fmt):
        try:
            stdout = str(fmt(result))
        except Exception:
            stdout = str(result.get("error") or result.get("result") or result)
    else:
        stdout = str(
            result.get("result")
            or result.get("echo")
            or result.get("error")
            or ("ok" if ok else "failed")
        )
    return {
        "ok": ok,
        "error": "" if ok else str(result.get("error") or "plugin_failed"),
        "error_code": "" if ok else str(result.get("error_code") or "plugin_error"),
        "data": result.get("data") if isinstance(result.get("data"), dict) else result,
        "stdout": stdout,
        "duration_ms": int(
            result.get("duration_ms")
            if result.get("duration_ms") is not None
            else (time.perf_counter() - t0) * 1000
        ),
        "exit_code": int(result.get("exit_code") if result.get("exit_code") is not None else (0 if ok else -1)),
        "command": str(result.get("command") or tool),
        "host": str(result.get("host") or ""),
    }


def list_plugin_manifests_for_catalog(*, include_unapproved: bool = True) -> List[Dict[str, Any]]:
    """市场展示：已加载 + 目录内未加载（proposed 等）。"""
    rows: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    reg = get_registry()
    for row in reg.as_catalog_rows():
        rows.append(row)
        seen.add(row["id"])

    root = default_plugins_dir()
    if not root.is_dir() or not include_unapproved:
        return rows
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        if child.name in seen:
            continue
        mp = child / "manifest.yaml"
        if not mp.is_file():
            mp = child / "manifest.yml"
        if not mp.is_file():
            continue
        m = _load_yaml(mp)
        if not m:
            continue
        name = str(m.get("name") or child.name).strip()
        rows.append(
            {
                "id": name,
                "title": str(m.get("title") or name),
                "category": str(m.get("category") or "plugin"),
                "skill_pack": str(m.get("skill_pack") or m.get("category") or "plugin"),
                "scope": "host" if bool(m.get("host_required")) else "local",
                "readonly": bool((m.get("security") or {}).get("read_only", True))
                if isinstance(m.get("security"), dict)
                else True,
                "status": str(m.get("status") or "unknown"),
                "loaded": False,
                "summary": str(m.get("description") or ""),
                "path": f"tools/plugins/{child.name}/",
                "version": str(m.get("version") or "0.0.0"),
                "author": str(m.get("author") or ""),
                "source": "plugins",
                "type": str(m.get("type") or ""),
                "host_required": bool(m.get("host_required")),
                "dependencies": _normalize_dependencies(m.get("dependencies")),
            }
        )
    return rows
