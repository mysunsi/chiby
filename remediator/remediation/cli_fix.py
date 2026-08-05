"""
终端入口：对失败命令启动 remediation 包装流程。

用法（仓库根目录且 PYTHONPATH 含当前工程）::

    python -m remediator.remediation.cli_fix "cp /x/y /z" --dry-run
    python -m remediator.remediation.cli_fix "false" --yes --max-retries 2
    python -m remediator.remediation.cli_fix "false" --yes --explain
"""
from __future__ import annotations

import argparse
import logging
import sys

from remediator.core.executor_wrapper import run_with_remediation


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AI 运维自愈 CLI（基于 executor_wrapper.run_with_remediation）"
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="要执行的命令（剩余参数均视为命令一部分；建议用引号包裹）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅分析 / 预检：不执行 RemediationController.run（修正命令不会下发）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="非交互（interactive=False），适合 CI",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="自愈最大重试次数",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="与 --dry-run 联用：完全不执行探测命令（无法获得真实 stderr）",
    )
    parser.add_argument(
        "--allow-high",
        action="store_true",
        help="允许 HIGH 风险命令进入闭环（默认拦截初始高危命令）",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="在 stdout 额外打印 Markdown 诊断报告（reports/{session_id}.md 同源内容）",
    )
    args = parser.parse_args()
    if not args.command:
        parser.error("请提供 command")

    cmd = " ".join(args.command).strip()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    diag_paths: list = []
    rw_kw = dict(
        dry_run=args.dry_run,
        interactive=not args.yes,
        max_retries=args.max_retries,
        dry_run_execute_probe=not args.no_probe,
        confirm_high_risk=args.allow_high,
        write_diagnostic_reports=True,
    )
    if args.explain:
        rw_kw["diagnostic_report_path"] = diag_paths
    try:
        out = run_with_remediation(cmd, **rw_kw)
    except KeyboardInterrupt:
        logging.warning("用户中断 (KeyboardInterrupt)")
        sys.exit(130)

    if out.stdout:
        sys.stdout.write(out.stdout)
        if not out.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if out.stderr:
        sys.stderr.write(out.stderr)
        if not out.stderr.endswith("\n"):
            sys.stderr.write("\n")

    if args.explain:
        if not diag_paths:
            logging.info("未生成诊断报告文件（写入失败或未启用）；无可打印内容。")
        for p in diag_paths:
            try:
                if p.is_file():
                    text = p.read_text(encoding="utf-8")
                    sys.stdout.write("\n")
                    sys.stdout.write("======== Diagnostic Report ========\n")
                    sys.stdout.write(text)
                    if not text.endswith("\n"):
                        sys.stdout.write("\n")
            except OSError as e:
                logging.warning("无法读取诊断报告 %s: %s", p, e)

    sys.exit(out.return_code)


if __name__ == "__main__":
    main()
