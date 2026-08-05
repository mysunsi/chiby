"""诊断相似历史案例检索（供编排注入 / IM 展示）。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from chibycore.knowledge_hub.tool_api import run_kb_get, run_kb_search

_STOP = {
    "的",
    "了",
    "吗",
    "呢",
    "啊",
    "吧",
    "一下",
    "帮我",
    "看看",
    "查看",
    "检查",
    "为什么",
    "怎么",
    "如何",
    "什么",
    "是否",
    "请",
    "下",
    "一下",
    "主机",
    "服务器",
}


def extract_keywords(text: str, *, limit: int = 8) -> List[str]:
    body = (text or "").strip().lower()
    if not body:
        return []
    # 英文/数字 token + 较长中文片段
    tokens = re.findall(r"[a-z][a-z0-9_\-\.]{1,40}|\d{2,}|[\u4e00-\u9fff]{2,12}", body)
    out: List[str] = []
    seen = set()
    for t in tokens:
        if t in _STOP or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def _host_scope_matches(case_scope: str, current_scope: str) -> bool:
    a = (case_scope or "").strip().lower()
    b = (current_scope or "").strip().lower()
    if not a or not b:
        return True
    return a in b or b in a or a == b


def search_similar_cases(
    user_input: str,
    *,
    host_scope: str = "",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """搜索与当前问题相似的 KB 故障案例。"""
    q = (user_input or "").strip()
    if not q:
        return []
    keywords = extract_keywords(q)
    query = " ".join(keywords) if keywords else q[:80]
    packed = run_kb_search(q=query, mode="kb", limit=max(limit * 3, 6))
    if not packed.get("ok"):
        return []
    cases: List[Dict[str, Any]] = []
    for hit in packed.get("results") or []:
        if str(hit.get("entry_type") or "") != "kb":
            continue
        eid = str(hit.get("entry_id") or "")
        detail = run_kb_get(entry_id=eid, entry_type="kb") if eid else {}
        if not detail.get("ok"):
            data = {
                "title": hit.get("title"),
                "symptom": hit.get("snippet"),
                "tags": hit.get("tags") or [],
            }
        else:
            data = detail
        notes = str(data.get("notes") or "")
        case_scope = ""
        m = re.search(r"适用范围[：:]\s*(.+)", notes)
        if m:
            case_scope = m.group(1).strip().splitlines()[0][:80]
        if not _host_scope_matches(case_scope, host_scope):
            continue
        cases.append(
            {
                "entry_id": eid,
                "title": str(data.get("title") or hit.get("title") or "")[:120],
                "symptom": str(data.get("symptom") or hit.get("snippet") or "")[:200],
                "root_cause": str(data.get("root_cause") or "")[:200],
                "solution": str(data.get("remediation") or "")[:200],
                "tags": list(data.get("tags") or hit.get("tags") or [])[:8],
                "host_scope": case_scope,
                "source": str(data.get("source") or ""),
                "score": hit.get("score"),
            }
        )
        if len(cases) >= limit:
            break
    return cases


def format_similar_cases_prompt(cases: List[Dict[str, Any]]) -> str:
    if not cases:
        return ""
    lines = ["[历史相似案例]"]
    for i, c in enumerate(cases, 1):
        lines.append(
            f"- 案例 {i}：{c.get('title') or '未命名'} → "
            f"根因：{(c.get('root_cause') or '—')[:80]} → "
            f"修复：{(c.get('solution') or '—')[:80]}"
        )
    lines.append("建议：结合上述经验优先验证同类根因，再决定取证命令。")
    return "\n".join(lines)


def format_similar_cases_ui(cases: List[Dict[str, Any]]) -> str:
    if not cases:
        return ""
    lines = [f"📚 找到 {len(cases)} 个相似历史案例"]
    for i, c in enumerate(cases, 1):
        lines.append(
            f"[案例 {i}] {c.get('title') or '未命名'} → "
            f"{(c.get('root_cause') or '—')[:40]} → "
            f"{(c.get('solution') or '—')[:40]}"
        )
    lines.append("正在结合这些经验排查...")
    return "\n".join(lines)
