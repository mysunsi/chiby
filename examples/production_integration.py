"""
Phase 4 生产集成示例。

运行前在仓库根目录（ai-ops-assistant）设置::

    set PYTHONPATH=%CD%
    python examples/production_integration.py

依赖：已配置 LLM API Key（dry-run 含 probe 时若需调用 LLM）。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remediator.core.executor_wrapper import (  # noqa: E402
    RiskLevel,
    analyze_only,
    infer_risk_level,
    run_with_remediation,
)


def _log_result(title: str, out) -> None:
    logging.info("=== %s ===", title)
    logging.info("return_code=%s", out.return_code)
    try:
        payload = json.loads(out.stdout) if out.stdout.strip().startswith("{") else None
        if payload:
            logging.info("stdout(json keys)=%s", list(payload.keys()))
        else:
            logging.info("stdout=%s", out.stdout[:500])
    except json.JSONDecodeError:
        logging.info("stdout=%s", out.stdout[:500])
    if out.stderr:
        logging.info("stderr=%s", out.stderr[:300])


def scenario_normal_repair() -> None:
    """场景 A：走完整自愈（非 dry-run，非交互模拟 CI）。"""
    logging.info("场景 A：正常修复流程（命令故意失败以触发解析）")
    out = run_with_remediation(
        "false",
        dry_run=False,
        interactive=False,
        max_retries=2,
    )
    _log_result("normal_repair", out)


def scenario_dry_run() -> None:
    """场景 B：dry-run（仅分析；默认仍 probe 一次原始命令）。"""
    logging.info("场景 B：dry-run 分析（false 立即失败，便于离线看 JSON）")
    out = run_with_remediation(
        "false",
        dry_run=True,
        interactive=False,
        max_retries=2,
        dry_run_execute_probe=True,
    )
    _log_result("dry_run", out)


def scenario_high_risk_block() -> None:
    """场景 C：高危初始命令在 confirm_high_risk=False 时被拦截。"""
    dangerous = "sudo rm -rf /"
    lvl = infer_risk_level(dangerous, "")
    logging.info("场景 C：infer_risk_level(%r) => %s", dangerous, lvl)
    assert lvl == RiskLevel.HIGH, "示例假定该命令为 HIGH"

    out = run_with_remediation(
        dangerous,
        dry_run=False,
        interactive=False,
        confirm_high_risk=False,
    )
    _log_result("high_risk_blocked", out)
    if out.return_code == 126 and "POLICY" in (out.stderr or ""):
        logging.info("已按预期拦截（未进入 RemediationController.run）")
    else:
        logging.warning("环境与正则可能导致未命中 HIGH；请检查 executor_wrapper 风险规则")


def scenario_analyze_only_api() -> None:
    """演示直接使用包装层 analyze_only 字典结果。"""
    logging.info("场景 D：analyze_only 字典（无 Controller.run）")
    rep = analyze_only("false", execute_probe=True)
    logging.info("analyze_only keys=%s", list(rep.keys()))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    scenario_high_risk_block()
    scenario_dry_run()
    scenario_analyze_only_api()
    try:
        scenario_normal_repair()
    except Exception as e:
        logging.warning("场景 A 可能因未配置 LLM 失败（可忽略）: %s", e)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
