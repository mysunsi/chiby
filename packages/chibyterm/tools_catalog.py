"""工具目录：官方内置 + tools/contrib 清单，供「工具市场」页与 API 使用。"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

from chibycore.repo_root import find_repo_root

_ROOT = find_repo_root()
_CONTRIB_MANIFEST = _ROOT / "tools" / "contrib" / "MANIFEST.json"


def _official_catalog() -> List[Dict[str, Any]]:
    """与 DEFAULT_ALLOWED_TOOLS 对齐的官方条目（展示用，非运行时白名单源）。"""
    return [
        {
            "id": "host_list",
            "title": "列出可见主机",
            "category": "host",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "返回当前 ACL 可见主机列表（无 host 参数）。",
        },
        {
            "id": "kb_search",
            "title": "运维知识库检索",
            "category": "knowledge",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "Chiby KnowledgeHub 短经验检索。",
        },
        {
            "id": "kb_get",
            "title": "运维知识库详情",
            "category": "knowledge",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "按 entry_id 读取知识条目全文。",
        },
        {
            "id": "kb_ingest",
            "title": "运维知识沉淀",
            "category": "knowledge",
            "scope": "local",
            "readonly": False,
            "status": "official",
            "summary": "写入经验（须确认卡；智能型/全能型）。",
        },
        {
            "id": "doc_search",
            "title": "企业文档语义检索",
            "category": "document",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "DocHub 长文档向量 TopK。",
        },
        {
            "id": "doc_get",
            "title": "企业文档片段读取",
            "category": "document",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "按 doc_id / chunk_id 取正文。",
        },
        {
            "id": "search_knowledge",
            "title": "统一知识检索",
            "category": "knowledge",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "并行检索 KnowledgeHub + DocHub，RRF 融合（Agent 主动查证）。",
        },
        {
            "id": "get_content",
            "title": "统一知识正文",
            "category": "knowledge",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "按 full_id 路由 kb_get / doc_get。",
        },
        {
            "id": "example_echo",
            "title": "Hello World 回显",
            "category": "example",
            "scope": "local",
            "readonly": True,
            "status": "official",
            "summary": "教学用：原样返回 text。见 terminal/mobile/example_tools.py。",
            "tutorial": "docs/extending-agent-tools.md",
        },
        {
            "id": "ssh_execute",
            "title": "SSH 执行",
            "category": "remote",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "单机 SSH 命令（确认策略随命令风险变化）。",
        },
        {
            "id": "winrm_execute",
            "title": "WinRM 执行",
            "category": "remote",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "单机 PowerShell / WinRM。",
        },
        {
            "id": "ssh_batch",
            "title": "SSH 批量",
            "category": "remote",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "多机 SSH 同一命令。",
        },
        {
            "id": "winrm_batch",
            "title": "WinRM 批量",
            "category": "remote",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "多机 WinRM。",
        },
        {
            "id": "remote_run",
            "title": "远端运行",
            "category": "remote",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "结构化远端执行（可流式）。",
        },
        {
            "id": "remote_list_dir",
            "title": "列目录",
            "category": "files",
            "scope": "host",
            "readonly": True,
            "status": "official",
            "summary": "远端列目录。",
        },
        {
            "id": "remote_read_file",
            "title": "读文件",
            "category": "files",
            "scope": "host",
            "readonly": True,
            "status": "official",
            "summary": "远端读文件（UTF-8，可分段）。",
        },
        {
            "id": "remote_write_file",
            "title": "写文件",
            "category": "files",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "远端写文件（须确认卡；可多机分发）。",
        },
        {
            "id": "remote_mkdir",
            "title": "建目录",
            "category": "files",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "远端创建目录。",
        },
        {
            "id": "remote_remove",
            "title": "删除路径",
            "category": "files",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "远端删文件/目录（须确认卡）。",
        },
        {
            "id": "remote_grep",
            "title": "代码搜索",
            "category": "devtools",
            "scope": "host",
            "readonly": True,
            "status": "official",
            "summary": "远端 grep（别名 remote_search）。",
        },
        {
            "id": "remote_diff",
            "title": "差异查看",
            "category": "devtools",
            "scope": "host",
            "readonly": True,
            "status": "official",
            "summary": "git diff 或备份对比。",
        },
        {
            "id": "remote_backup",
            "title": "备份",
            "category": "devtools",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "改前备份到目标机 .hermes_backups/。",
        },
        {
            "id": "remote_restore",
            "title": "回滚恢复",
            "category": "devtools",
            "scope": "host",
            "readonly": False,
            "status": "official",
            "summary": "从备份恢复（别名 remote_rollback；须确认卡）。",
        },
        {
            "id": "remote_syntax_check",
            "title": "语法检查",
            "category": "devtools",
            "scope": "host",
            "readonly": True,
            "status": "official",
            "summary": "远端 python/js 语法检查。",
        },
        {
            "id": "remote_logs",
            "title": "日志尾部",
            "category": "devtools",
            "scope": "host",
            "readonly": True,
            "status": "official",
            "summary": "远端日志 tail。",
        },
    ]


def _load_contrib() -> List[Dict[str, Any]]:
    if not _CONTRIB_MANIFEST.is_file():
        return []
    try:
        data = json.loads(_CONTRIB_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("contrib MANIFEST 读取失败: %s", e)
        return []
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in tools:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or "").strip()
        if not tid:
            continue
        row = dict(item)
        row["id"] = tid
        row.setdefault("status", "community")
        row.setdefault("scope", "local")
        row.setdefault("readonly", True)
        row.setdefault("category", "community")
        out.append(row)
    return out


def build_skill_packs(plugins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 skill_pack（缺省 category）聚合技能包。"""
    packs: Dict[str, Dict[str, Any]] = {}
    for p in plugins or []:
        pid = str(p.get("id") or "").strip()
        if not pid:
            continue
        pack_id = str(p.get("skill_pack") or p.get("category") or "misc").strip() or "misc"
        bucket = packs.setdefault(
            pack_id,
            {
                "id": pack_id,
                "title": pack_id,
                "tool_ids": [],
                "loaded_count": 0,
                "total": 0,
            },
        )
        bucket["tool_ids"].append(pid)
        bucket["total"] += 1
        if p.get("loaded") or p.get("status") == "loaded":
            bucket["loaded_count"] += 1
    # 友好标题
    titles = {
        "remote_fs": "远端文件",
        "remote_shell": "远端命令",
        "knowledge": "运维知识",
        "document": "企业文档",
        "host": "主机可见性",
        "example": "示例",
    }
    out = []
    for pack_id, bucket in sorted(packs.items(), key=lambda x: x[0]):
        bucket["title"] = titles.get(pack_id, pack_id)
        bucket["tool_ids"] = sorted(bucket["tool_ids"])
        out.append(bucket)
    return out


def filter_catalog(
    catalog: Dict[str, Any],
    *,
    pack: str = "",
    tool_type: str = "",
    loaded_only: bool = False,
    q: str = "",
) -> Dict[str, Any]:
    """对 catalog 做轻量过滤（返回新 dict，不改入参）。"""
    pack = (pack or "").strip()
    tool_type = (tool_type or "").strip()
    q = (q or "").strip().lower()

    def _match(item: Dict[str, Any]) -> bool:
        if pack and str(item.get("skill_pack") or item.get("category") or "") != pack:
            return False
        if tool_type and str(item.get("type") or "") != tool_type:
            return False
        if loaded_only and not (item.get("loaded") or item.get("status") == "loaded"):
            return False
        if q:
            hay = " ".join(
                str(item.get(k) or "")
                for k in ("id", "title", "summary", "category", "skill_pack", "type", "version")
            ).lower()
            if q not in hay:
                return False
        return True

    out = dict(catalog)
    for key in ("official", "community", "plugins"):
        rows = [r for r in (catalog.get(key) or []) if isinstance(r, dict) and _match(r)]
        out[key] = rows
        out[f"{key}_count" if key != "plugins" else "plugin_count"] = len(rows)
    # official_count naming
    out["official_count"] = len(out.get("official") or [])
    out["community_count"] = len(out.get("community") or [])
    out["plugin_count"] = len(out.get("plugins") or [])
    out["total"] = out["official_count"] + out["community_count"] + out["plugin_count"]
    out["packs"] = build_skill_packs(out.get("plugins") or [])
    out["filtered"] = bool(pack or tool_type or loaded_only or q)
    return out


def build_tools_catalog() -> Dict[str, Any]:
    official = _official_catalog()
    community = _load_contrib()
    plugins: List[Dict[str, Any]] = []
    try:
        from chibyterm.tools_plugin_loader import list_plugin_manifests_for_catalog

        plugins = list_plugin_manifests_for_catalog(include_unapproved=True)
    except Exception as e:
        logger.warning("插件目录读取失败: %s", e)
    plugin_ids = {str(p.get("id") or "") for p in plugins}
    # 已加载/登记的插件从 official 去重（如 example_echo）
    official = [o for o in official if o.get("id") not in plugin_ids]
    packs = build_skill_packs(plugins)
    loaded_n = sum(1 for p in plugins if p.get("loaded") or p.get("status") == "loaded")
    return {
        "ok": True,
        "phase": 6,
        "official_count": len(official),
        "community_count": len(community),
        "plugin_count": len(plugins),
        "plugin_loaded_count": loaded_n,
        "pack_count": len(packs),
        "total": len(official) + len(community) + len(plugins),
        "official": official,
        "community": community,
        "plugins": plugins,
        "packs": packs,
        "docs": {
            "extending": "docs/extending-agent-tools.md",
            "plugin_arch": "docs/tool-plugin-architecture.md",
            "host_contract": "docs/host-plugin-contract.md",
            "marketplace": "docs/tool-marketplace-phase6.md",
            "contrib": "tools/contrib/README.md",
        },
        "contrib_manifest": str(_CONTRIB_MANIFEST.relative_to(_ROOT)).replace("\\", "/"),
    }


