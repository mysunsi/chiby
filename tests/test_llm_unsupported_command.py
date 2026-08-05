# -*- coding: utf-8 -*-
"""UNSUPPORTED / UNSUPPORTED: 原因 不得当作可执行命令下发。"""
from __future__ import annotations

from terminal.llm_shell import (
    LLMPromptProcessor,
    looks_like_unsupported_command,
    sanitize_prompt_result_command,
)
from terminal.models import PromptResult


def test_looks_like_unsupported_exact_and_reason():
    assert looks_like_unsupported_command("UNSUPPORTED")
    assert looks_like_unsupported_command("unsupported")
    assert looks_like_unsupported_command(
        "UNSUPPORTED: 输入内容模糊，无法确定要执行的运维操作"
    )
    assert looks_like_unsupported_command(
        "UNSUPPORTED: 输入内容模糊\n更多说明"
    )
    assert not looks_like_unsupported_command("free -h")
    assert not looks_like_unsupported_command("echo UNSUPPORTED")


def test_sanitize_strips_unsupported_with_reason():
    r = PromptResult(
        explanation="意图不明确",
        command="UNSUPPORTED: 输入内容模糊，无法确定要执行的运维操作",
        should_execute=True,
    )
    out = sanitize_prompt_result_command(r)
    assert out.command == ""
    assert out.should_execute is False
    assert "意图不明确" in (out.explanation or "")
    assert "输入内容模糊" in (out.explanation or "")


def test_parse_llm_response_unsupported_with_reason_not_executable():
    pp = LLMPromptProcessor.__new__(LLMPromptProcessor)
    text = (
        "[EXPLAIN] 无法理解用户意图，请补充具体目标\n"
        "[COMMAND] UNSUPPORTED: 输入内容模糊，无法确定要执行的运维操作\n"
        "[DANGEROUS] false\n"
    )
    result = pp._parse_llm_response(text)
    assert (result.command or "") == ""
    assert result.should_execute is False
    assert "无法理解" in (result.explanation or "") or "模糊" in (result.explanation or "")


def test_parse_llm_response_exact_unsupported():
    pp = LLMPromptProcessor.__new__(LLMPromptProcessor)
    text = "[EXPLAIN] 无法识别\n[COMMAND] UNSUPPORTED\n[DANGEROUS] false\n"
    result = pp._parse_llm_response(text)
    assert (result.command or "") == ""
    assert result.should_execute is False
