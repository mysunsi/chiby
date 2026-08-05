"""结果说明：free -h 规则回退应回答内存问题，而非空泛「命令已成功」。"""
from __future__ import annotations

from chibyterm.llm_explain import rule_explain_fallback


SAMPLE_FREE = """\
free -h
              total        used        free      shared  buff/cache   available
Mem:          3.7Gi       2.8Gi       115Mi       1.0Mi       775Mi       623Mi
Swap:         1.0Gi       355Mi       668Mi
\x1b[01;32msunsi@main\x1b[00m:\x1b[01;34m~\x1b[00m$
"""


def test_rule_explain_free_h_answers_memory_question():
    md = rule_explain_fallback(
        command="free -h",
        output_tail=SAMPLE_FREE,
        status="pass",
        exit_code=0,
        user_question="查看内存使用情况",
    )
    assert "命令已成功执行" not in md
    assert "3.7Gi" in md or "623Mi" in md
    assert "内存" in md
    assert "available" not in md.lower() or "可用" in md
    # 不应整段粘贴带 ANSI 的提示符
    assert "\x1b" not in md


def test_rule_explain_generic_still_works_without_domain():
    md = rule_explain_fallback(
        command="echo hi",
        output_tail="hi\n",
        status="pass",
        exit_code=0,
        user_question="打个招呼",
    )
    assert "命令已成功执行" in md
    assert "echo hi" in md
