"""群发命令：逐机结果说明汇总 + LLM 跨主机对比分析报告。"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_COMMON_HARD = """
硬性要求：
- 只依据给定各机说明与摘要，禁止编造数字、费用或主机状态
- 禁止逐台复制粘贴「结果说明」原文；先汇总再下结论
- 输出简洁中文 Markdown（不要用代码围栏包裹全文）
"""

_REPORT_SYSTEM_OPS = """你是资深运维顾问，面向**管理层/领导**撰写「多主机群发巡检」的**总体分析报告**。
输入是同一命令在多台主机上的执行结果说明，你必须先汇总再下结论，**不要写成多份单机报告的拼接**。

输出结构固定为（四段都必须完整写出，禁止省略任何一段）：
1. **总体结论**（2～4 句）：覆盖面、健康态势、最需关注点
2. **关键指标对比**：一张 Markdown 表格（主机、状态、关键指标或失败原因）；异常主机排前
3. **形势研判**：共性与显著差异（至少 2 句）
4. **行动建议**（必须 2～4 条可拍板动作，带优先级；即使整体健康也要给「持续观察 / 基线复核」类建议）

全文一般 500～1000 字，语气正式、便于转发汇报。不得在「形势研判」处截断，不得省略「行动建议」。
""" + _COMMON_HARD

_REPORT_SYSTEM_RISK = """你是安全合规顾问，请以**风险/合规**视角分析以下多主机巡检结果，写给安全合规部门领导。

输出结构固定为：
1. **总体风险评级**：明确给出 **高 / 中 / 低**（禁止用「可能」「大概」等模糊词）
2. **合规偏离项**：逐条列出违反基线或异常的主机（主机名 + 偏离说明）
3. **整改优先级**：按风险分为 **P0 / P1 / P2**，每条对应可执行整改指令
4. **追责建议**：仅当有明确违规操作迹象时给出；否则写「未见明确违规操作，暂不追责」

语气坚决、可执行；直接给出整改指令，不要空话。
""" + _COMMON_HARD

_REPORT_SYSTEM_CAPACITY = """你是基础架构与容量规划顾问，请以**资源容量规划**视角分析以下多主机巡检结果，写给基础架构/财务相关领导。

输出结构固定为：
1. **总体水位评估**：基于现有输出评估 CPU/内存/磁盘等水位（信息不足处标明）
2. **瓶颈预测**：指出未来可能先耗尽资源的主机（数据不足时写「需补充历史监控数据」）
3. **扩容建议**：按紧急程度排序的可执行建议
4. **成本估算**：对建议扩容方案给出大致费用量级；数据不足时注明「需补充历史监控数据 / 报价清单」

禁止编造精确账单数字；缺数据必须显式标注。
""" + _COMMON_HARD

_REPORT_SYSTEM_STRATEGY = """你是技术战略顾问，请以**技术战略**视角提炼以下多主机巡检结果，写给 CEO/CTO。

输出结构固定为：
1. **一句话结论**（适合短信/IM 预览）
2. **核心数据**（不超过 3 个关键指标）
3. **风险与机遇**（当前架构的优势与隐患，各一句即可）
4. **年度建议**（基础设施投入方向，1～2 条）

**总篇幅不超过 200 字**，适合邮件摘要；禁止展开逐机长文。
""" + _COMMON_HARD

_REPORT_SYSTEM_BY_TONE = {
    "ops": _REPORT_SYSTEM_OPS,
    "risk": _REPORT_SYSTEM_RISK,
    "capacity": _REPORT_SYSTEM_CAPACITY,
    "strategy": _REPORT_SYSTEM_STRATEGY,
}

_REPORT_SYSTEM_OPS_EN = (
    "You are a senior ops advisor writing an executive multi-host inspection report "
    "for leadership. Markdown sections: Executive conclusion; Key metrics table; "
    "Situation assessment; Prioritized actions. Do not invent data or concatenate "
    "per-host essays."
)
_REPORT_SYSTEM_RISK_EN = (
    "Write from a risk/compliance perspective for security leadership. Structure: "
    "Overall risk rating (High/Medium/Low); Compliance deviations (per host); "
    "Remediation priority P0/P1/P2 with actionable commands; Accountability note "
    "(or state none). No vague wording."
)
_REPORT_SYSTEM_CAPACITY_EN = (
    "Write from a capacity-planning perspective for infra/finance. Structure: "
    "Overall headroom (CPU/mem/disk); Bottleneck forecast; Expansion recommendations "
    "by urgency; Rough cost estimate. If data is insufficient, write "
    "'need historical monitoring data'."
)
_REPORT_SYSTEM_STRATEGY_EN = (
    "Write a CEO/CTO strategy digest under 200 words. Structure: One-line conclusion "
    "(IM-ready); Up to 3 key metrics; Risks & opportunities; Annual infra investment "
    "direction. No per-host essays."
)

_REPORT_SYSTEM_EN_BY_TONE = {
    "ops": _REPORT_SYSTEM_OPS_EN,
    "risk": _REPORT_SYSTEM_RISK_EN,
    "capacity": _REPORT_SYSTEM_CAPACITY_EN,
    "strategy": _REPORT_SYSTEM_STRATEGY_EN,
}


def system_prompt_for_tone(report_tone: str, *, ui_locale: str = "zh-CN") -> str:
    from chibyterm.broadcast_settings import normalize_report_tone

    tone = normalize_report_tone(report_tone)
    if (ui_locale or "").startswith("en"):
        return _REPORT_SYSTEM_EN_BY_TONE.get(tone, _REPORT_SYSTEM_OPS_EN)
    text = _REPORT_SYSTEM_BY_TONE.get(tone, _REPORT_SYSTEM_OPS)
    if ui_locale == "zh-TW":
        return text.replace("中文", "繁體中文")
    return text


@dataclass
class BroadcastHostResult:
    session_id: str
    host_label: str = ""
    status: str = "unknown"  # pass | fail | blocked | error | unknown
    ok: bool = False
    stdout_tail: str = ""
    explain_md: str = ""
    error: str = ""
    command: str = ""


@dataclass
class BroadcastJob:
    job_id: str
    command: str = ""
    initiator_session_id: str = ""
    session_ids: List[str] = field(default_factory=list)
    results: List[BroadcastHostResult] = field(default_factory=list)
    report_md: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    # running | exec_done | report_pending | done | error
    phase: str = "running"
    report_tone: str = "ops"
    nl_intent: str = ""
    commands_by_session: Dict[str, str] = field(default_factory=dict)
    host_ids: List[str] = field(default_factory=list)


def compute_stats(results: Sequence[BroadcastHostResult]) -> Dict[str, Any]:
    total = len(results)
    ok_n = sum(1 for r in results if (r.status or "").lower() == "pass" or r.ok)
    fail_n = sum(
        1 for r in results if (r.status or "").lower() in ("fail", "blocked", "error")
    )
    unknown_n = max(0, total - ok_n - fail_n)
    return {
        "total": total,
        "ok": ok_n,
        "fail": fail_n,
        "unknown": unknown_n,
    }


def rule_comparative_report(
    *,
    command: str,
    results: Sequence[BroadcastHostResult],
    ui_locale: str = "zh-CN",
    report_tone: str = "ops",
) -> str:
    """LLM 不可用时的结构化汇总（按汇报口吻分段）。"""
    from chibyterm.broadcast_settings import normalize_report_tone, tone_label

    tone = normalize_report_tone(report_tone)
    stats = compute_stats(results)
    cmd = (command or "").strip() or "(空)"
    n = stats["total"]

    def _one_line(r: BroadcastHostResult) -> str:
        bit = (r.explain_md or r.error or r.stdout_tail or "").strip()
        bit = re.sub(r"^#+\s*", "", bit)
        bit = re.sub(r"\*\*?结论[：:]\s*", "", bit)
        bit = re.sub(r"\s+", " ", bit)
        return bit[:120] + ("…" if len(bit) > 120 else "")

    ordered = sorted(
        results,
        key=lambda r: 0 if (r.status or "").lower() in ("fail", "blocked", "error") else 1,
    )

    if ui_locale == "en":
        rating = "High" if stats["fail"] else ("Medium" if stats["unknown"] else "Low")
        if tone == "risk":
            lines = [
                f"**Overall risk rating:** **{rating}** "
                f"({stats['fail']} failed / {n} hosts; command `{cmd[:120]}`).",
                "",
                "**Compliance deviations**",
            ]
            for r in ordered:
                if (r.status or "").lower() in ("fail", "blocked", "error") or not r.ok:
                    lines.append(
                        f"- **{r.host_label or r.session_id}**: {_one_line(r) or r.status}"
                    )
            if len(lines) == 3:
                lines.append("- None detected from this snapshot.")
            lines += ["", "**Remediation priority**", ""]
            if stats["fail"]:
                lines.append("- **P0**: Fix failed hosts (OS mismatch / command errors).")
            lines.append("- **P1**: Review hosts with anomalous metrics.")
            lines += ["", "**Accountability:** No clear policy violation in this snapshot."]
            return "\n".join(lines)
        if tone == "capacity":
            lines = [
                f"**Headroom:** Snapshot of **{n}** host(s) via `{cmd[:120]}` — "
                f"{stats['ok']} ok, {stats['fail']} failed.",
                "",
                "**Bottleneck forecast:** Need historical monitoring data "
                "for CPU/memory/disk trends.",
                "",
                "| Host | Status | Snapshot |",
                "| --- | --- | --- |",
            ]
            for r in ordered:
                lines.append(
                    f"| {r.host_label or r.session_id} | {r.status or '?'} | {_one_line(r) or '—'} |"
                )
            lines += [
                "",
                "**Expansion (urgency):** Triage failed hosts first; then review pressure hosts.",
                "",
                "**Cost estimate:** Need historical monitoring data / vendor quotes.",
            ]
            return "\n".join(lines)
        if tone == "strategy":
            return (
                f"**One-liner:** Fleet check `{cmd[:80]}` → "
                f"{stats['ok']}/{n} ok"
                + (f", {stats['fail']} failed — prioritize remediation." if stats["fail"] else ".")
                + "\n\n"
                f"**Key metrics:** hosts={n}; ok={stats['ok']}; fail={stats['fail']}.\n\n"
                "**Risk & opportunity:** Cross-OS command risk vs. unified ops visibility.\n\n"
                "**Annual ask:** Standardize OS fleets and invest in monitoring baselines."
            )
        # ops
        lines = [
            f"**Executive summary:** Inspected **{n}** host(s) with `{cmd[:160]}` — "
            f"**{stats['ok']}** ok, **{stats['fail']}** failed, **{stats['unknown']}** unknown.",
            "",
            "**Comparison**",
            "",
            "| Host | Status | Snapshot |",
            "| --- | --- | --- |",
        ]
        for r in ordered:
            lines.append(
                f"| {r.host_label or r.session_id} | {r.status or '?'} | {_one_line(r) or '—'} |"
            )
        lines += ["", "**Recommended actions**", ""]
        if stats["fail"]:
            lines.append("1. Triage failed hosts first (OS mismatch or command errors).")
        lines.append("2. Follow up hosts with elevated resource pressure.")
        return "\n".join(lines)

    # 简体 / 繁体共用骨架（繁体仅改标题词）
    tw = ui_locale == "zh-TW"
    rating = "高" if stats["fail"] else ("中" if stats["unknown"] else "低")
    label_hint = tone_label(tone, ui_locale)

    if tone == "risk":
        h1 = "**總體風險評級：**" if tw else "**总体风险评级：**"
        h2 = "**合規偏離項**" if tw else "**合规偏离项**"
        h3 = "**整改優先級**" if tw else "**整改优先级**"
        h4 = "**追責建議：**" if tw else "**追责建议：**"
        none_dev = "- 本次快照未見明確偏離。" if tw else "- 本次快照未见明确偏离。"
        lines = [
            f"{h1}**{rating}**（失败 {stats['fail']} / 共 {n} 台；命令 `{cmd[:120]}`；口吻：{label_hint}）。",
            "",
            h2,
        ]
        any_dev = False
        for r in ordered:
            if (r.status or "").lower() in ("fail", "blocked", "error") or not r.ok:
                any_dev = True
                lines.append(
                    f"- **{r.host_label or r.session_id}**：{_one_line(r) or r.status}"
                )
        if not any_dev:
            lines.append(none_dev)
        lines += ["", h3, ""]
        if stats["fail"]:
            lines.append(
                "- **P0**：立即修复失败主机（系统不兼容或命令错误），按 OS 分组重跑巡检。"
            )
        lines.append("- **P1**：复核指标异常主机并补齐基线对照。")
        lines.append("- **P2**：统一命令集与权限基线，避免跨 OS 误检。")
        lines += [
            "",
            (h4 + "未见明确违规操作，暂不追责。")
            if not tw
            else (h4 + "未見明確違規操作，暫不追責。"),
        ]
        return "\n".join(lines)

    if tone == "capacity":
        h1 = "**總體水位評估：**" if tw else "**总体水位评估：**"
        lines = [
            f"{h1}对 **{n}** 台执行 `{cmd[:120]}` — 成功 {stats['ok']}，失败 {stats['fail']}。"
            " 当前仅为单次快照，趋势判断需补充历史监控数据。",
            "",
            "**瓶颈预测：** 需补充历史监控数据（CPU/内存/磁盘趋势）。",
            "",
            "| 主机 | 状态 | 摘要 |",
            "| --- | --- | --- |",
        ]
        for r in ordered:
            lines.append(
                f"| {r.host_label or r.session_id} | {r.status or '?'} | {_one_line(r) or '—'} |"
            )
        lines += [
            "",
            "**扩容建议：** 先处理失败主机，再评估资源紧张主机的扩容/清理。",
            "",
            "**成本估算：** 需补充历史监控数据 / 报价清单。",
        ]
        return "\n".join(lines)

    if tone == "strategy":
        fail_bit = (
            f"，失败 {stats['fail']} 台需优先处置。"
            if stats["fail"]
            else "，整体可控。"
        )
        return (
            f"**一句话结论：** 集群巡检 `{cmd[:60]}` → {stats['ok']}/{n} 成功{fail_bit}\n\n"
            f"**核心数据：** 主机 {n}；成功 {stats['ok']}；失败 {stats['fail']}。\n\n"
            "**风险与机遇：** 跨系统命令差异是隐患；统一巡检提升可见性。\n\n"
            "**年度建议：** 按 OS 分池治理，并投入基线监控与容量看板。"
        )

    # ops
    h1 = "**總體結論：**" if tw else "**总体结论：**"
    h2 = "**關鍵指標對比**" if tw else "**关键指标对比**"
    h3 = "**行動建議**" if tw else "**行动建议**"
    lines = [
        f"{h1}对 **{n}** 台主机执行 `{cmd[:160]}` — "
        f"成功 **{stats['ok']}**，失败 **{stats['fail']}**，未知 **{stats['unknown']}**。",
        "",
        h2,
        "",
        "| 主机 | 状态 | 一句话摘要 |",
        "| --- | --- | --- |",
    ]
    for r in ordered:
        lines.append(
            f"| {r.host_label or r.session_id} | {r.status or '?'} | {_one_line(r) or '—'} |"
        )
    lines += ["", h3, ""]
    if stats["fail"]:
        lines.append("1. **优先处理失败主机**（如 Windows/Linux 命令不兼容或执行错误）。")
    lines.append("2. **跟进指标异常主机**，必要时扩容或排查进程。")
    lines.append("3. 后续群发请按操作系统分组，避免跨 OS 使用同一命令。")
    if n <= 1:
        lines.append("")
        lines.append("_仅 1 台主机，属单点检查，非集群汇报。_")
    return "\n".join(lines)


def _trim_report(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    fence = re.match(r"^```(?:markdown|md)?\s*([\s\S]*?)```\s*$", s, re.I)
    if fence:
        s = fence.group(1).strip()
    return s[:6000]


def comparative_report_md(
    *,
    command: str,
    user_question: str = "",
    results: Sequence[BroadcastHostResult],
    ui_locale: str = "zh-CN",
    report_tone: str = "ops",
) -> str:
    """优先 LLM 总体分析报告；失败则规则汇总。"""
    from chibyterm.broadcast_settings import normalize_report_tone, tone_label
    from chibyterm.ui_locale import ai_language_instruction, normalize_ui_locale

    ui_locale = normalize_ui_locale(ui_locale)
    tone = normalize_report_tone(report_tone)
    if not results:
        if ui_locale == "en":
            return "**Executive summary:** No host results to compare."
        if ui_locale == "zh-TW":
            return "**總體結論：** 無主機結果可對比。"
        return "**总体结论：** 无主机结果可对比。"

    try:
        from chibycore.llm_config import get_effective_llm_settings
        from chibycore.llm_providers import get_llm

        llm = get_llm()
        if llm is None or not getattr(llm, "is_available", False):
            return rule_comparative_report(
                command=command,
                results=results,
                ui_locale=ui_locale,
                report_tone=tone,
            )
        no_think = bool(get_effective_llm_settings().get("no_think", True))
    except Exception as exc:
        logger.info("comparative report llm init failed: %s", exc)
        return rule_comparative_report(
            command=command,
            results=results,
            ui_locale=ui_locale,
            report_tone=tone,
        )

    blocks: List[str] = []
    for i, r in enumerate(results, 1):
        label = r.host_label or r.session_id or f"host-{i}"
        body = (r.explain_md or "").strip()
        if not body:
            body = (r.error or r.stdout_tail or "").strip()[:1200] or "(empty)"
        body = body[:900]
        blocks.append(
            f"### Host {i}: {label}\n"
            f"- status: {r.status}\n"
            f"- notes:\n{body}"
        )

    tone_name = tone_label(tone, ui_locale)
    if ui_locale == "en":
        briefing = (
            f"Report tone: **{tone_name}** ({tone}). "
            "Write ONE unified fleet report — aggregate first; do NOT paste per-host essays. "
            "Must fully include: overall conclusion, key metrics comparison table, situation assessment, "
            "and recommended actions (2–4 items). Never omit recommended actions."
        )
        user_msg = (
            f"{briefing}\n\n"
            f"User intent: {(user_question or '').strip() or '(broadcast command)'}\n"
            f"Command: {(command or '').strip()}\n"
            f"Hosts: {len(results)}\n\n" + "\n\n".join(blocks)
        )
    elif ui_locale == "zh-TW":
        briefing = (
            f"彙報口吻：**{tone_name}**（{tone}）。"
            "請寫一份統一總體報告：先彙總再下結論，不要逐台複述長文。"
            "必須完整包含：總體結論、關鍵指標對比表、形勢研判、行動建議（2～4 條），禁止省略行動建議。"
        )
        user_msg = (
            f"{briefing}\n\n"
            f"使用者意圖：{(user_question or '').strip() or '（群發命令）'}\n"
            f"命令：{(command or '').strip()}\n"
            f"主機數：{len(results)}\n\n" + "\n\n".join(blocks)
        )
    else:
        briefing = (
            f"汇报口吻：**{tone_name}**（{tone}）。"
            "请写一份统一总体分析报告：先汇总再下结论，不要逐台复述长文。"
            "必须完整包含：总体结论、关键指标对比表、形势研判、行动建议（2～4 条），禁止省略行动建议。"
        )
        user_msg = (
            f"{briefing}\n\n"
            f"用户意图：{(user_question or '').strip() or '（群发命令）'}\n"
            f"命令：{(command or '').strip()}\n"
            f"主机数：{len(results)}\n\n" + "\n\n".join(blocks)
        )

    system = system_prompt_for_tone(tone, ui_locale=ui_locale) + ai_language_instruction(
        ui_locale
    )
    max_tokens = 900 if tone == "strategy" else 2200

    try:
        text = llm.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            no_think=no_think,
        )
        md = _trim_report(text or "")
        if md:
            return md
    except Exception as exc:
        logger.warning("comparative report llm failed: %s", exc)

    return rule_comparative_report(
        command=command,
        results=results,
        ui_locale=ui_locale,
        report_tone=tone,
    )


# 进程内最近 job（调试 / 可选 REST）
_BROADCAST_JOBS: Dict[str, BroadcastJob] = {}
_MAX_JOBS = 24


def store_broadcast_job(job: BroadcastJob) -> None:
    _BROADCAST_JOBS[job.job_id] = job
    if len(_BROADCAST_JOBS) > _MAX_JOBS:
        # 删最旧
        for k in list(_BROADCAST_JOBS.keys())[: len(_BROADCAST_JOBS) - _MAX_JOBS]:
            _BROADCAST_JOBS.pop(k, None)


def get_broadcast_job(job_id: str) -> Optional[BroadcastJob]:
    return _BROADCAST_JOBS.get(job_id)


def job_to_api_dict(job: BroadcastJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "command": job.command,
        "initiator_session_id": job.initiator_session_id,
        "session_ids": list(job.session_ids),
        "host_ids": list(getattr(job, "host_ids", None) or []),
        "nl_intent": getattr(job, "nl_intent", "") or "",
        "commands_by_session": dict(getattr(job, "commands_by_session", None) or {}),
        "phase": job.phase,
        "stats": dict(job.stats or {}),
        "report_md": job.report_md or "",
        "report_tone": getattr(job, "report_tone", None) or "ops",
        "results": [
            {
                "session_id": r.session_id,
                "host_label": r.host_label,
                "status": r.status,
                "ok": r.ok,
                "stdout_tail": (r.stdout_tail or "")[:4000],
                "explain_md": r.explain_md or "",
                "error": r.error or "",
                "command": r.command or "",
            }
            for r in job.results
        ],
    }
