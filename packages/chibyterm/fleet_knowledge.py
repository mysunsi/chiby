"""Fleet 报告 ↔ KnowledgeHub：关联案例、入库预填、定时异常模式提示。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence


def build_fleet_search_query(
    *,
    nl_intent: str = "",
    command: str = "",
    report_md: str = "",
) -> str:
    parts = [
        (nl_intent or "").strip(),
        (command or "").strip()[:120],
    ]
    # 从报告抽少量关键词行（告警/异常）
    md = report_md or ""
    for ln in md.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if re.search(r"异常|告警|失败|高|满|超时|error|fail|warn|critical", s, re.I):
            parts.append(s[:80])
        if len(parts) >= 6:
            break
    q = " ".join(x for x in parts if x)
    return q[:200] or "巡检"


def search_fleet_related_cases(
    *,
    nl_intent: str = "",
    command: str = "",
    report_md: str = "",
    host_scope: str = "",
    limit: int = 3,
) -> List[Dict[str, Any]]:
    from chibycore.knowledge_hub.similar_cases import search_similar_cases

    q = build_fleet_search_query(
        nl_intent=nl_intent, command=command, report_md=report_md
    )
    return search_similar_cases(q, host_scope=host_scope or "", limit=limit)


def prefill_fleet_kb_template(
    *,
    nl_intent: str = "",
    command: str = "",
    report_md: str = "",
    host_scope: str = "",
    stats: Optional[Dict[str, Any]] = None,
    job_id: str = "",
    report_tone: str = "",
) -> Dict[str, Any]:
    """生成「存为知识模板」预填字段。"""
    from datetime import datetime, timezone

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intent = (nl_intent or command or "Fleet 巡检").strip()
    title = f"Fleet巡检_{intent[:40]}_{day}".replace(" ", "")
    st = stats or {}
    ok = st.get("ok", st.get("success", "?"))
    fail = st.get("fail", st.get("failed", "?"))
    total = st.get("total", "?")
    symptom = (
        f"意图：{intent}\n"
        f"命令：{(command or '')[:200]}\n"
        f"结果：成功 {ok} / 失败 {fail} / 共 {total}"
    )
    # 报告正文截取作根因/方案草稿
    body = (report_md or "").strip()
    root = ""
    solution = ""
    if body:
        # 尝试抓「行动建议 / 根因」段
        m = re.search(
            r"(?:根因|行动建议|建议|Recommendations?)[：:\s]*([\s\S]{20,600})",
            body,
            re.I,
        )
        if m:
            root = m.group(1).strip()[:500]
            solution = root
        else:
            root = body[:500]
            solution = body[:500]
    tags = ["fleet_report", "巡检"]
    if report_tone:
        tags.append(str(report_tone))
    return {
        "title": title[:120],
        "symptom": symptom[:2000],
        "root_cause": root or "见 Fleet 报告正文",
        "solution": solution or "见 Fleet 报告正文",
        "host_scope": (host_scope or "").strip()[:80],
        "trace_id": (job_id or "").strip(),
        "tags": tags,
        "category": "system_monitor",
        "source": "fleet_report",
        "notes": (body[:3000] if body else "来自 Fleet 报告"),
    }


def detect_repeat_failure_pattern(
    recent_events: Sequence[Dict[str, Any]],
    *,
    min_repeats: int = 3,
) -> Optional[Dict[str, Any]]:
    """从最近事件中检测**连续**相同异常（从最新往回数）。

    ``recent_events`` 建议按时间新→旧（``query_platform_audit`` 默认）。
    """
    if not recent_events:
        return None
    streak = 0
    host_ids: List[str] = []
    name = ""
    cmd = ""
    summary = ""
    trace_ids: List[str] = []
    for ev in recent_events:
        if not isinstance(ev, dict):
            continue
        et = str(ev.get("event_type") or "")
        if et not in ("scheduled_task_run", "fleet_execute"):
            continue
        outcome = str(ev.get("outcome") or "")
        if outcome not in ("failure", "partial"):
            # 成功则打断连续
            break
        streak += 1
        hosts = ev.get("host_ids") if isinstance(ev.get("host_ids"), list) else []
        if not host_ids:
            host_ids = list(hosts)
        meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
        if not name:
            name = str(meta.get("name") or meta.get("schedule_id") or "")[:80]
        if not cmd:
            cmd = str(ev.get("command") or "")[:120]
        if not summary:
            summary = str(ev.get("result_summary") or "")[:200]
        tid = str(ev.get("trace_id") or "")
        if tid:
            trace_ids.append(tid)
    if streak < min_repeats:
        return None
    label = name or cmd or "任务"
    return {
        "pattern": "repeat_failure",
        "count": streak,
        "name": name,
        "command": cmd,
        "host_ids": host_ids,
        "last_summary": summary,
        "trace_ids": trace_ids[:5],
        "hint": f"检测到「{label}」连续 {streak} 次异常，是否沉淀为知识模板？",
    }
