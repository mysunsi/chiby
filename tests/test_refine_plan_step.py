"""计划步骤「重试」时 LLM 单步修订。"""
from __future__ import annotations

from unittest.mock import MagicMock

from terminal.llm_shell import LLMPromptProcessor


def test_refine_plan_step_command_no_llm():
    p = LLMPromptProcessor.__new__(LLMPromptProcessor)
    p._llm_available = False
    p._llm = None
    r = LLMPromptProcessor.refine_plan_step_command(
        p,
        plan_explanation="x",
        step_title="t",
        prior_command="echo 1",
        user_note="改成 echo 2",
        session_context="",
        runtime_hint="",
        shell_profile="unix",
    )
    assert r.should_execute is False
    assert "未配置" in (r.explanation or "")


def test_refine_plan_step_command_mock_llm():
    p = LLMPromptProcessor.__new__(LLMPromptProcessor)
    p._llm_available = True
    p._llm = MagicMock()
    p._llm.chat.return_value = (
        "[EXPLAIN] 按补充改为 echo 2\n[COMMAND] echo 2\n[DANGEROUS] false\n"
    )
    r = LLMPromptProcessor.refine_plan_step_command(
        p,
        plan_explanation="plan",
        step_title="step1",
        prior_command="echo 1",
        user_note="输出 2",
        session_context="out",
        runtime_hint="Linux bash",
        shell_profile="unix",
    )
    assert r.command
    assert "echo 2" in r.command
