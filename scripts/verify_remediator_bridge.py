#!/usr/bin/env python3
"""
验证「闭环修复 → remediator_fix_bridge」是否与 Terminal 共用同一套 LLM 配置。

前置（仓库根目录 ai-ops-assistant）：
  pip install -r requirements.txt
  pip install -e remediator

配置其一即可：
  - data/llm_config.json（与 ChibyTerm 相同）
  - 或环境变量里已有 OPENAI_API_KEY / DEEPSEEK_API_KEY 等（见 chibycore.llm_providers）

用法：
  python scripts/verify_remediator_bridge.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    from chibycore.executor_contract import ClosurePayload, RiskLevel
    from chibycore.remediator_fix_bridge import call_remediator_for_fix_commands

    # 典型「文件不存在」失败，stderr 有明确错误信息，更利于 remediator 解析 + LLM 出修复
    miss = "/tmp/ops_remediator_verify_missing_file_12345"
    cmd = f"cat {miss}"
    err = f"cat: {miss}: No such file or directory\n"
    cp = ClosurePayload(
        trace_id="verify-remediator-bridge",
        raw_command=cmd,
        effective_command=cmd,
        transport="local",
        risk_level=RiskLevel.LOW,
        exit_code=1,
        stdout="",
        stderr=err,
    )
    fixes = call_remediator_for_fix_commands([cp], shell_profile="unix")
    print()
    print("call_remediator_for_fix_commands 返回：")
    if not fixes:
        print("  （空列表）")
        print()
        print("常见原因：")
        print("  - OPS_CLOSURE_REMEDIATOR_FIX=0 已关闭 remediator 分支")
        print("  - LLM 未配置：DEEPSEEK_API_KEY / OPENAI_* / LLM_API_KEY 或 data/llm_config.json")
        print("  - remediator 未安装：pip install -e remediator")
        print("  - propose_remediation failed：常为 Python 3.8 + 新版 litellm/pydantic，")
        print("    可升级 Python≥3.10 或 pip install \"litellm>=1.40,<1.53\"（见日志全文）")
        print("  - 查看上方 INFO/WARNING 日志")
        return 1
    for i, line in enumerate(fixes, start=1):
        print(f"  {i}. {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
