"""
回归测试：remediation 集成（executor_wrapper + ExecutorBackend），不修改 remediation/ 实现。

需网络/LLM 的调用通过 stub 与 mock 关闭。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 在导入依赖 litellm 的 remediator 子包之前注入 stub，避免未安装 litellm 时导入失败
if "litellm" not in sys.modules:
    _litellm_stub = MagicMock()
    _litellm_stub.completion = MagicMock(
        return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="{}"))])
    )
    sys.modules["litellm"] = _litellm_stub

from remediator.core.executor_backends import ExecutorBackend, ExecutorResult, LocalSubprocessBackend
from remediator.core.metrics import MetricsCollector, RemediationMetrics
from remediator.core.executor_wrapper import run_with_remediation
from remediator.remediation.models import ErrorCategory, KnowledgeRecord, LLMRemediationJSON


# 「文件不存在」stderr，不触发 lite_fixer（避免先于 KB 命中）
FIX_NO_SUCH = (
    "cat: /tmp/file_kb_regression_missing_xyz: No such file or directory\n"
)
MISSING_CAT_CMD = "cat /tmp/file_kb_regression_missing_xyz"


class StubBackend(ExecutorBackend):
    """按次序返回预设结果的本地后端（便于断言调用次序）。"""

    def __init__(self, outcomes: list[ExecutorResult]) -> None:
        self._outcomes = outcomes
        self._idx = 0

    def run(self, command: str, *, timeout: int = 300) -> ExecutorResult:
        if self._idx >= len(self._outcomes):
            raise AssertionError(f"unexpected extra run({command!r})")
        r = self._outcomes[self._idx]
        self._idx += 1
        return ExecutorResult(
            command=command,
            stdout=r.stdout,
            stderr=r.stderr,
            return_code=r.return_code,
        )


def _ok() -> ExecutorResult:
    return ExecutorResult(command="", stdout="ok", stderr="", return_code=0)


def _fail_nosuch() -> ExecutorResult:
    return ExecutorResult(
        command="",
        stdout="",
        stderr=FIX_NO_SUCH,
        return_code=1,
    )


@pytest.mark.unit
def test_kb_hit_prefers_kb_no_llm(tmp_path) -> None:
    """KB 命中首轮修正：第二次执行成功；不应调用 LLM。"""
    kb_cmd = "echo kb_fix_success"
    kb = MagicMock()
    kb.query_best_match.return_value = KnowledgeRecord(
        error_category=ErrorCategory.FILE_NOT_FOUND,
        original_command=MISSING_CAT_CMD,
        fixed_command=kb_cmd,
        root_cause="kb case",
    )

    # 首轮 probe 失败 + Controller 内再次执行初始命令失败 + KB 修正成功
    stub = StubBackend([_fail_nosuch(), _fail_nosuch(), _ok()])

    def _no_llm(*_a, **_kw):
        raise AssertionError("LLM propose_remediation 不应被调用（KB 首轮命中）")

    monkey_metrics = MetricsCollector(tmp_path / "m.jsonl")

    with patch("remediator.remediation.loop.propose_remediation", side_effect=_no_llm):
        out = run_with_remediation(
            MISSING_CAT_CMD,
            backend=stub,
            knowledge_base=kb,
            interactive=False,
            confirm_high_risk=False,
            record_metrics=True,
            metrics_collector=monkey_metrics,
            write_diagnostic_reports=False,
        )

    assert out.return_code == 0
    payload = json.loads(out.stdout)
    assert payload["termination"] == "success"
    assert payload["final_command"] == kb_cmd
    assert stub._idx == 3

    lines = (tmp_path / "m.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert lines
    row = json.loads(lines[-1])
    assert row["kb_hit"] is True
    assert row["llm_calls"] == 0


@pytest.mark.unit
def test_loop_detected_semantic_after_repeated_same_stderr() -> None:
    """
    语义雷同类循环：首轮修正失败；第二轮提案与上一轮命令相似度触发 LOOP_DETECTED_SEMANTIC。

    说明：判定发生在第二次提案阶段（与上一轮 prev_fix 比较），因此 backend.run 通常仅
    「初始失败 + 第一轮修正失败」两次；仍满足「 stderr 相同」的失败语义。
    """
    fix_a = "run_install_nginx_modules_ssl_certbot_step_one"
    fix_b = "run_install_nginx_modules_ssl_certbot_step_two"

    # 三次失败：包装层首轮 probe + Controller 内再次执行初始命令 + 第一轮修正
    stub = StubBackend([_fail_nosuch(), _fail_nosuch(), _fail_nosuch()])

    proposals = [
        LLMRemediationJSON(
            root_cause="r1",
            fixed_command=fix_a,
            risk_warning="",
        ),
        LLMRemediationJSON(
            root_cause="r2",
            fixed_command=fix_b,
            risk_warning="",
        ),
    ]

    kb = MagicMock()
    kb.query_best_match.return_value = None

    with patch("remediator.remediation.loop.propose_remediation", side_effect=proposals):
        out = run_with_remediation(
            MISSING_CAT_CMD,
            backend=stub,
            knowledge_base=kb,
            interactive=False,
            max_retries=3,
            confirm_high_risk=False,
            record_metrics=False,
            write_diagnostic_reports=False,
        )

    assert out.return_code >= 1
    payload = json.loads(out.stdout)
    assert payload["termination"] == "loop_detected_semantic"
    assert stub._idx == 3


@pytest.mark.unit
def test_high_risk_initial_blocked_no_execute() -> None:
    """HIGH 初始命令在 confirm_high_risk=False 时被拦截，且不调用 backend.run。"""
    stub = MagicMock(spec=ExecutorBackend)
    out = run_with_remediation(
        "sudo rm -rf /",
        backend=stub,
        interactive=False,
        confirm_high_risk=False,
        record_metrics=False,
        write_diagnostic_reports=False,
    )
    assert out.stderr == "POLICY_BLOCK_HIGH_INITIAL"
    assert out.return_code == 126
    stub.run.assert_not_called()


@pytest.mark.unit
def test_metrics_collector_safe_append_kb_flags(tmp_path, monkeypatch) -> None:
    """验证 MetricsCollector.safe_append 被调用且 kb_hit 字段符合场景。"""
    monkeypatch.delenv("REMEDIATION_METRICS_DISABLE", raising=False)

    calls: list[RemediationMetrics] = []

    def tracking(collector: MetricsCollector | None, metrics: RemediationMetrics) -> None:
        calls.append(metrics)
        if collector is None:
            return
        collector.append(metrics)

    kb = MagicMock()
    kb.query_best_match.return_value = KnowledgeRecord(
        error_category=ErrorCategory.FILE_NOT_FOUND,
        original_command=MISSING_CAT_CMD,
        fixed_command="echo ok",
    )
    stub = StubBackend([_fail_nosuch(), _fail_nosuch(), _ok()])

    col_kb = MetricsCollector(tmp_path / "kb.jsonl")
    with patch.object(MetricsCollector, "safe_append", side_effect=tracking):
        run_with_remediation(
            MISSING_CAT_CMD,
            backend=stub,
            knowledge_base=kb,
            interactive=False,
            record_metrics=True,
            metrics_collector=col_kb,
            write_diagnostic_reports=False,
        )

    assert calls
    assert any(m.kb_hit is True for m in calls)

    col_block = MetricsCollector(tmp_path / "block.jsonl")
    calls.clear()
    stub2 = MagicMock(spec=ExecutorBackend)
    with patch.object(MetricsCollector, "safe_append", side_effect=tracking):
        run_with_remediation(
            "sudo rm -rf /",
            backend=stub2,
            interactive=False,
            confirm_high_risk=False,
            record_metrics=True,
            metrics_collector=col_block,
            write_diagnostic_reports=False,
        )

    assert any(m.kb_hit is False and m.risk_blocked is True for m in calls)


@pytest.mark.unit
def test_fixtures_mock_metrics_jsonl_is_valid() -> None:
    """静态样例：每行 JSON 含 kb_hit 等字段，供报表/联调参考。"""
    p = Path(__file__).resolve().parent / "fixtures" / "mock_metrics.jsonl"
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for ln in lines:
        row = json.loads(ln)
        assert "kb_hit" in row
        assert "llm_calls" in row
        assert "fix_type" in row


@pytest.mark.unit
def test_default_backend_backward_compat_runs_local_subprocess_unpatched() -> None:
    """LocalSubprocessBackend 可与原 run_command 行为对齐（无害命令）。"""
    b = LocalSubprocessBackend()
    r = b.run('python -c "raise SystemExit(0)"', timeout=30)
    assert r.return_code == 0
    assert "raise SystemExit(0)" in r.command
