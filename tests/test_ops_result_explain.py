"""运维模式：折叠执行卡 + 结果梳理（LLM / 规则回退）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from terminal.mobile.models import ExecResult
from terminal.mobile.orchestrator import (
    _format_exec_digest_for_user,
    _ops_rule_explain_fallback,
    _ops_rule_explain_job_fallback,
    call_ops_llm_explain,
    call_ops_llm_explain_job,
)


def _er(**kwargs) -> ExecResult:
    base = dict(
        ok=True,
        host_id="5d418c8e",
        command="systemctl is-active nginx",
        exit_code=0,
        stdout_tail="active\n",
        duration_ms=20,
    )
    base.update(kwargs)
    return ExecResult(**base)


def test_exec_digest_uses_fold_markers():
    text = _format_exec_digest_for_user(
        [_er(command="systemctl status nginx", stdout_tail="Active: active (running)\n")]
    )
    assert "**远端执行结果**" in text
    assert "`$ systemctl status nginx` · 已成功执行" in text
    assert "<<<EXEC_BODY>>>" in text
    assert "<<<END_EXEC_BODY>>>" in text
    assert "Active: active (running)" in text


def test_exec_digest_strips_agent_closure_noise():
    raw = (
        "[Agent闭环·成功] host=`h1` fix_rounds=0\n"
        "$ systemctl is-active nginx\n"
        "结束原因：success_initial\n"
        "exit=0\n"
        "active\n"
    )
    text = _format_exec_digest_for_user(
        [_er(stdout_tail=raw, command="systemctl is-active nginx")]
    )
    assert "Agent闭环" not in text
    assert "success_initial" not in text
    assert "active" in text


def test_rule_explain_fallback_nginx_active():
    out = _ops_rule_explain_fallback([_er()])
    assert "正在运行" in out or "结论" in out


def test_call_ops_llm_explain_uses_question_and_results():
    mock_llm = MagicMock()
    mock_llm.is_available = True
    mock_llm.chat.return_value = (
        "**结论：nginx 正在正常运行。**\n\n"
        "- 服务状态为 active\n"
        "- 无需处理"
    )
    with patch("chibycore.llm_providers.get_llm", return_value=mock_llm):
        text = call_ops_llm_explain(
            user_question="nginx 状态",
            host_label="main.sunsi.cn",
            results=[_er()],
        )
    assert "nginx" in text.lower() or "正在" in text
    assert mock_llm.chat.called
    messages = mock_llm.chat.call_args[0][0]
    system_content = messages[0]["content"]
    assert "后续建议" in system_content
    assert "不要硬凑" in system_content or "空话" in system_content
    assert "表格" in system_content
    assert "不要写「结果梳理」" in system_content or "结果梳理" in system_content
    user_content = messages[1]["content"]
    assert "nginx 状态" in user_content
    assert "systemctl is-active nginx" in user_content


def test_call_ops_llm_explain_returns_empty_when_no_llm():
    mock_llm = MagicMock()
    mock_llm.is_available = False
    with patch("chibycore.llm_providers.get_llm", return_value=mock_llm):
        assert (
            call_ops_llm_explain(
                user_question="nginx 状态",
                host_label="h1",
                results=[_er()],
            )
            == ""
        )


def test_ops_rule_explain_job_fallback():
    text = _ops_rule_explain_job_fallback(
        "任务：检测内存（2 台）\n├─ yl：FreeGB 0.92 ✓",
        job_name="检测内存",
    )
    assert "检测内存" in text
    assert "结论" in text


def test_call_ops_llm_explain_job_uses_multi_host_rows():
    mock_llm = MagicMock()
    mock_llm.is_available = True
    mock_llm.chat.return_value = (
        "**结论：两台机器内存均可用。**\n\n"
        "| 主机 | 可用内存 |\n| --- | --- |\n"
        "| yl | 0.92 GB |\n| main | 1.1Gi |"
    )
    with patch("chibycore.llm_providers.get_llm", return_value=mock_llm):
        text = call_ops_llm_explain_job(
            user_question="内存还剩多少",
            job_name="检测内存",
            summary="任务：检测内存（2 台）\n├─ yl：FreeGB 0.92 ✓\n└─ main：总量 3.7Gi · 可用 1.1Gi ✓",
            host_rows=[
                {
                    "label": "yl.sunsi.cn",
                    "status": "ok",
                    "command": "free…",
                    "preview": '{"FreeGB":0.92}',
                    "detail": '{"TotalGB":4,"FreeGB":0.92}',
                },
                {
                    "label": "main.sunsi.cn",
                    "status": "ok",
                    "command": "free -h",
                    "preview": "总量 3.7Gi · 可用 1.1Gi",
                    "detail": "Mem: 3.7Gi … 1.1Gi",
                },
            ],
        )
    assert "内存" in text or "主机" in text or "|" in text
    user_content = mock_llm.chat.call_args[0][0][1]["content"]
    assert "多主机" in user_content
    assert "内存还剩多少" in user_content
    assert "main.sunsi.cn" in user_content
