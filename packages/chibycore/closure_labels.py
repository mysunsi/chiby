# -*- coding: utf-8 -*-
"""闭环判定文案：对用户可见处用中文，内部码仅作兼容映射。"""
from __future__ import annotations

from typing import Optional

# 机器码 → 用户可读说明（兼容历史日志 / 旧 API）
_JUDGE_REASON_ZH = {
    "llm_parse_fail_exit_code_fallback": "智能判定结果未能解析，已改按退出码判断",
    "llm_unavailable_exit_code_fallback": "智能判定暂不可用，已改按退出码判断",
    "unknown_mode_fallback_exit_code": "成败模式未知，已改按退出码判断",
    "exit_code": "按退出码判断",
    "llm": "按智能判定",
}


def humanize_judge_reason(reason: Optional[str]) -> str:
    """将 LLM 判定 reason / 回退码转为可读中文；未知原文尽量保留。"""
    r = (reason or "").strip()
    if not r:
        return ""
    if r in _JUDGE_REASON_ZH:
        return _JUDGE_REASON_ZH[r]
    if r.startswith("llm_error_fallback:"):
        detail = r.split(":", 1)[-1].strip()[:120]
        return "智能判定调用失败，已改按退出码判断" + (f"（{detail}）" if detail else "")
    # 已是中文或自然语言则原样返回
    return r


def format_both_mode_detail(
    *,
    exit_ok: bool,
    llm_ok: bool,
    reason: Optional[str] = None,
) -> str:
    """both 模式下的 outcome_detail（用户可见）。"""
    exit_part = "退出码通过" if exit_ok else "退出码未通过"
    llm_part = "智能判定通过" if llm_ok else "智能判定未通过"
    base = f"{exit_part}；{llm_part}"
    hr = humanize_judge_reason(reason)
    if hr and hr not in (base, "按退出码判断", "按智能判定"):
        # 仅在有额外说明（如回退原因、LLM 自然语言）时追加
        if reason and reason.strip() in _JUDGE_REASON_ZH:
            return f"{base}（{hr}）"
        if reason and str(reason).startswith("llm_error_fallback:"):
            return f"{base}（{hr}）"
        if reason and reason.strip() not in ("llm", "exit_code") and hr:
            return f"{base}。说明：{hr}"
    return base


def format_verify_message(
    *,
    passed: bool,
    exit_ok: Optional[bool],
    llm_ok: Optional[bool],
    success_mode: str,
    judge_reason: Optional[str] = None,
    outcome_detail: Optional[str] = None,
    stderr_tail: Optional[str] = None,
    stdout_tail: Optional[str] = None,
) -> str:
    """时间线「验证结果」步骤的用户可读描述。"""
    mode = (success_mode or "exit_code").strip().lower()
    head = "验证通过" if passed else "验证未通过"
    bits = []
    if mode == "exit_code":
        if exit_ok is True:
            bits.append("退出码正常")
        elif exit_ok is False:
            bits.append("退出码异常")
    elif mode == "llm":
        if llm_ok is True:
            bits.append("智能判定认为成功")
        elif llm_ok is False:
            bits.append("智能判定认为未成功")
        bits.append(humanize_judge_reason(judge_reason) or "")
    else:  # both / default
        if exit_ok is True:
            bits.append("退出码正常")
        elif exit_ok is False:
            bits.append("退出码异常")
        if llm_ok is True:
            bits.append("智能判定通过")
        elif llm_ok is False:
            bits.append("智能判定未通过")
        hr = humanize_judge_reason(judge_reason)
        # 回退类说明有价值；纯 llm 无意义
        if hr and (judge_reason or "").strip() in _JUDGE_REASON_ZH:
            bits.append(hr)
        elif hr and (judge_reason or "").startswith("llm_error_fallback:"):
            bits.append(hr)
        elif hr and (judge_reason or "").strip() not in ("", "llm", "exit_code"):
            # 自然语言说明
            if len(hr) <= 160 and "exit=" not in hr:
                bits.append(hr)

    # 现象摘录（用户最关心）
    se = (stderr_tail or "").strip().replace("\n", " ")[:160]
    so = (stdout_tail or "").strip().replace("\n", " ")[:120]
    if se:
        bits.append("报错：" + se)
    elif so and not passed:
        bits.append("输出：" + so)
    elif so and passed and len(so) <= 80:
        bits.append("输出：" + so)

    body = "；".join(b for b in bits if b)
    if body:
        return f"{head}：{body}"
    # 兜底：旧 outcome_detail 若已是可读中文可用
    od = humanize_outcome_detail(outcome_detail or "")
    if od and "exit=" not in od:
        return f"{head}：{od}"
    return head


def humanize_outcome_detail(detail: Optional[str]) -> str:
    """兼容旧格式 exit=False llm=False (code)。"""
    d = (detail or "").strip()
    if not d:
        return ""
    if d.startswith("exit=") and " llm=" in d:
        # exit=False llm=False (llm_parse_fail_exit_code_fallback)
        try:
            left, _, rest = d.partition("(")
            parts = left.strip().split()
            exit_ok = None
            llm_ok = None
            for p in parts:
                if p.startswith("exit="):
                    exit_ok = p.split("=", 1)[1].lower() in ("true", "1", "yes")
                elif p.startswith("llm="):
                    llm_ok = p.split("=", 1)[1].lower() in ("true", "1", "yes")
            reason = rest.rstrip(")").strip() if rest else ""
            if exit_ok is not None and llm_ok is not None:
                return format_both_mode_detail(
                    exit_ok=exit_ok, llm_ok=llm_ok, reason=reason
                )
        except Exception:
            pass
    if d in _JUDGE_REASON_ZH:
        return _JUDGE_REASON_ZH[d]
    return humanize_judge_reason(d) or d
