"""闭环步骤的认知摘要与因果链（启发式、无额外 LLM 调用）。

用于降低 WinRM 包装输出 / 长 stdout 的阅读负载：结构化「发生了什么 / 结果 / 下一步提示」
与「触发 → 网关 → 执行 → 判定」纯文本因果链。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from chibycore.closure_retry_runner import ClosureStepRecord


def _snip(cmd: str, n: int = 72) -> str:
    s = " ".join((cmd or "").replace("\r", " ").replace("\n", " ").split())
    if len(s) > n:
        return s[: n - 1] + "…"
    return s


def _transport_hint(rec: ClosureStepRecord) -> str:
    if rec.payload is not None:
        return str(rec.payload.transport or "?")
    if rec.result is not None:
        return str(rec.result.transport or "?")
    return "?"


def build_closure_cognitive_summary(
    rec: ClosureStepRecord, *, success_mode: str
) -> Tuple[str, str, str]:
    """三层短文案：发生了什么 / 结果如何 / 下一步可读提示。"""
    sm = (success_mode or "exit_code").strip().lower()
    phase = rec.phase or ""
    fr = int(rec.fix_round or 0)
    gate_ok = bool(rec.gateway_allowed)
    cmd_snip = _snip(rec.command or "")

    if rec.pending_change_control and not gate_ok:
        what = f"变更冻结窗口内提交了命令「{cmd_snip}」，已进入待审批队列。"
        outcome = "尚未执行：等待变更审批。"
        nxt = "审批通过后可从待办队列放行后再执行。"
        return what, outcome, nxt

    if not gate_ok:
        gr = (rec.gateway_reason or "策略拒绝").strip()
        phase_cn = "首轮" if phase == "initial" else f"第 {fr} 轮修复"
        what = f"{phase_cn}尝试命令「{cmd_snip}」，网关未放行。"
        gd = getattr(rec, "gateway_detail", None) or {}
        tag_bits = []
        dc = str(gd.get("denial_category") or "").strip()
        if dc == "policy_deny":
            tag_bits.append("策略黑名单")
        elif dc == "change_window_hold":
            tag_bits.append("变更冻结窗口")
        rk = str(gd.get("rule_kind") or "").strip()
        if rk and rk not in tag_bits:
            tag_bits.append(rk)
        if gd.get("override_requires_approval"):
            tag_bits.append("需审批放行")
        tags = (" · " + " · ".join(tag_bits)) if tag_bits else ""
        outcome = f"未执行远端命令。原因：{gr[:280]}{tags}"
        nxt = "请改写为低风险命令，或调整策略 / 在允许窗口内重试。"
        return what, outcome, nxt

    phase_cn = "首轮执行" if phase == "initial" else f"自动修复第 {fr} 轮"
    tx = _transport_hint(rec)
    what = f"{phase_cn}：「{cmd_snip}」。传输：{tx}。"

    rc = rec.result.exit_code if rec.result else None
    ex_ok = rec.exit_ok
    lj_ok = rec.llm_judge_ok

    if sm == "exit_code":
        ok_final = bool(ex_ok)
        outcome = (
            f"退出码 {rc}，按 exit 判定为{'成功' if ok_final else '失败'}。"
            + (f" {rec.outcome_detail}" if rec.outcome_detail else "")
        )
    elif sm == "llm":
        ok_final = bool(lj_ok)
        lr = (rec.llm_judge_reason or "").strip()
        outcome = (
            f"AI 判定：{'成功' if ok_final else '未通过'}"
            + (f"（{lr[:240]}）" if lr else "")
        )
    else:
        ok_final = bool(ex_ok and lj_ok)
        outcome = (
            f"exit={'✓' if ex_ok else '✗'} · AI={'✓' if lj_ok else '✗'}"
            + (f" · {rec.outcome_detail}" if rec.outcome_detail else "")
        )

    if ok_final:
        nxt = "本步已满足当前成败模式；若无后续步骤则闭环即将结束。"
    else:
        if phase == "initial":
            nxt = "在配置允许时将进入自动修复轮次（受 max_fix_attempts 上限约束）。"
        else:
            nxt = "将继续下一轮修复尝试，或在用尽轮次后停止并给出原因。"

    return what.strip(), outcome.strip(), nxt.strip()


def build_closure_causal_chain(rec: ClosureStepRecord, *, success_mode: str) -> List[Dict[str, str]]:
    """四段因果节点：trigger / gateway / execute / evaluate —— 供前端箭头渲染。"""
    sm = (success_mode or "exit_code").strip().lower()
    gate_ok = bool(rec.gateway_allowed)

    nodes: List[Dict[str, str]] = [
        {"key": "trigger", "label": "触发", "status": "ok"},
    ]

    if rec.pending_change_control and not gate_ok:
        nodes.append({"key": "gateway", "label": "网关·冻结", "status": "warn"})
        nodes.append({"key": "execute", "label": "执行", "status": "skip"})
        nodes.append({"key": "evaluate", "label": "判定", "status": "skip"})
        return nodes

    if not gate_ok:
        gd = getattr(rec, "gateway_detail", None) or {}
        cat = str(gd.get("denial_category") or "").strip()
        rk = str(gd.get("rule_kind") or "").strip()
        if cat == "policy_deny" and rk:
            glab = "网关·" + rk[:48]
        elif cat == "change_window_hold":
            glab = "网关·冻结"
        else:
            glab = "网关"
        nodes.append({"key": "gateway", "label": glab, "status": "fail"})
        nodes.append({"key": "execute", "label": "执行", "status": "skip"})
        nodes.append({"key": "evaluate", "label": "判定", "status": "skip"})
        return nodes

    nodes.append({"key": "gateway", "label": "网关", "status": "ok"})

    if rec.result is None:
        exec_st = "warn"
    elif rec.exit_ok is True:
        exec_st = "ok"
    elif rec.exit_ok is False:
        exec_st = "fail"
    else:
        exec_st = "warn"

    nodes.append({"key": "execute", "label": "执行", "status": exec_st})

    if sm == "exit_code":
        if rec.exit_ok is True:
            ev_st = "ok"
        elif rec.exit_ok is False:
            ev_st = "fail"
        else:
            ev_st = "warn"
    elif sm == "llm":
        if rec.llm_judge_ok is True:
            ev_st = "ok"
        elif rec.llm_judge_ok is False:
            ev_st = "fail"
        else:
            ev_st = "warn"
    else:
        ex_ok = rec.exit_ok is True
        lj_ok = rec.llm_judge_ok is True
        if ex_ok and lj_ok:
            ev_st = "ok"
        elif not ex_ok and not lj_ok:
            ev_st = "fail"
        else:
            ev_st = "warn"

    nodes.append({"key": "evaluate", "label": "判定", "status": ev_st})

    return nodes


def causal_chain_arrow_text(nodes: List[Dict[str, Any]]) -> str:
    """单行「触发✓ → 网关✓ → …」用于列表 / 聊天摘要复制。"""
    icon = {"ok": "✓", "fail": "✗", "warn": "⚠", "skip": "⊘", "pending": "…"}
    parts: List[str] = []
    for n in nodes:
        st = str(n.get("status") or "")
        parts.append(f"{n.get('label', '?')}{icon.get(st, '?')}")
    return " → ".join(parts)
