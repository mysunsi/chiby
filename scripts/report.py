#!/usr/bin/env python3
"""
读取 remediation_metrics.jsonl 并输出汇总（依赖 rich）。

用法（仓库根目录）::

    python scripts/report.py
    python scripts/report.py --file data/remediation_metrics.jsonl
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from rich.console import Console
from rich.table import Table


def main() -> None:
    parser = argparse.ArgumentParser(description="Remediation 指标报表")
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        default=Path("data") / "remediation_metrics.jsonl",
        help="metrics.jsonl 路径",
    )
    args = parser.parse_args()
    path = args.file
    console = Console()

    if not path.is_file():
        console.print(f"[red]文件不存在: {path}[/red]")
        raise SystemExit(1)

    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    n = len(rows)
    if n == 0:
        console.print("[yellow]无数据[/yellow]")
        return

    success_n = sum(1 for r in rows if r.get("success"))
    failures_n = n - success_n
    kb_hits = sum(1 for r in rows if r.get("kb_hit"))
    dry_n = sum(1 for r in rows if r.get("dry_run"))
    retries_list = [int(r.get("retries", 0) or 0) for r in rows]
    cats = Counter(str(r.get("error_category") or "unknown") for r in rows)

    success_rate = success_n / n if n else 0.0
    kb_den = failures_n if failures_n else 0
    kb_rate = (kb_hits / kb_den) if kb_den else 0.0
    avg_retries = statistics.mean(retries_list) if retries_list else 0.0

    summary = Table(title="Remediation 指标汇总", show_header=True, header_style="bold")
    summary.add_column("指标", style="cyan")
    summary.add_column("值", justify="right")

    summary.add_row("总执行次数（会话数）", str(n))
    summary.add_row("成功率", f"{success_rate:.1%}")
    summary.add_row("失败次数 (success=False)", str(failures_n))
    summary.add_row("KB 命中次数", str(kb_hits))
    summary.add_row("KB 命中率 (KB Hits / Failures)", f"{kb_rate:.1%}" if kb_den else "N/A")
    summary.add_row("平均重试次数（fix 下发次数）", f"{avg_retries:.2f}")
    summary.add_row("dry_run 会话数", str(dry_n))

    console.print(summary)

    top = Table(title="Top 5 error_category", show_header=True)
    top.add_column("error_category", style="magenta")
    top.add_column("次数", justify="right")
    for cat, cnt in cats.most_common(5):
        top.add_row(cat, str(cnt))
    console.print(top)


if __name__ == "__main__":
    main()
