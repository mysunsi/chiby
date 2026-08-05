"""跨轮诊断焦点锚定。"""

from __future__ import annotations

from terminal.mobile.diag_focus import (
    enrich_with_diag_focus,
    extract_diag_focus,
    looks_like_trend_followup,
    merge_diag_focus,
)


def test_looks_like_trend_followup():
    assert looks_like_trend_followup("对比昨天同一时刻，今天的 error 量是增加了还是减少了？")
    assert looks_like_trend_followup("环比有没有好转")
    assert not looks_like_trend_followup("磁盘还剩多少")


def test_extract_ssi_scale_finding():
    text = (
        "根因分析：SSI 应用层出现 87,807 条错误，主要为 Connect refused 与 license ASSERT。\n"
        "8888 端口缺失加剧了重试风暴。\n"
    )
    focus = extract_diag_focus(text)
    blob = " ".join(focus).lower()
    assert "87807" in blob.replace(",", "") or "87,807" in " ".join(focus)
    assert "ssi" in blob or "assert" in blob or "refused" in blob


def test_enrich_injects_focus_for_trend():
    focus = [
        "SSI 应用层 87,807 条错误（Connect refused / license ASSERT）",
        "8888 端口缺失",
    ]
    out = enrich_with_diag_focus(
        "对比昨天同一时刻，今天的 error 量是增加了还是减少了？",
        focus=focus,
    )
    assert out.startswith("[会话诊断焦点")
    assert "87,807" in out or "87807" in out.replace(",", "")
    assert "必须优先延续" in out
    assert "禁止" in out and "系统日志" in out


def test_enrich_noop_for_plain_query():
    out = enrich_with_diag_focus("磁盘还剩多少", focus=["SSI 错误很多"])
    assert out == "磁盘还剩多少"


def test_merge_prefers_new():
    merged = merge_diag_focus(
        ["旧焦点 nginx 挂了"],
        ["新焦点 SSI 87807 条错误 Connect refused"],
    )
    assert merged[0].startswith("新焦点")
