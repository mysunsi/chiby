"""
灰度发布 CLI 工具。

用法:
  python -m chibycore.cli run-rollout --command "部署配置" --hosts 172.25.87.85,172.25.87.86 --percents 10,50,100
  python -m chibycore.cli run-rollout --command "重启服务" --hosts host1,host2 --gate-process nginx --dry-run
  python -m chibycore.cli list-rollouts
  python -m chibycore.cli status-rollout <id>
  python -m chibycore.cli cancel-rollout <id>
  python -m chibycore.cli rollback-rollout <id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from typing import Optional

from .gate import (
    GateChecker,
    GateConfig,
    GateKind,
    create_cmd_gate,
    create_http_gate,
    create_port_gate,
    create_process_gate,
    create_promql_gate,
)
from .rollout import RolloutEngine, RolloutReport, split_batches
from .schemas import GateConfig as SchemaGateConfig
from .schemas import RolloutRequest


def _parse_gate_args(args: argparse.Namespace) -> Optional[GateConfig]:
    """根据 CLI 参数构造 GateConfig"""
    if args.gate_http:
        return create_http_gate(args.gate_http)
    elif args.gate_port:
        parts = args.gate_port.split(":")
        host = parts[0] if len(parts) > 1 else "localhost"
        try:
            port = int(parts[-1])
        except ValueError:
            port = 80
        return create_port_gate(host, port)
    elif args.gate_process:
        return create_process_gate(args.gate_process)
    elif args.gate_promql:
        parts = args.gate_promql.split("|")
        if len(parts) >= 3:
            return create_promql_gate(parts[0], parts[1], parts[2])
        elif len(parts) == 2:
            return create_promql_gate(parts[0], parts[1])
        else:
            return create_promql_gate(parts[0])
    elif args.gate_cmd:
        return create_cmd_gate(args.gate_cmd)
    return None


async def _run_ssh_for_host(
    host: str,
    ssh_user: str,
    ssh_password: str,
    command: str,
) -> tuple[bool, str, dict]:
    """为单个主机执行 SSH 命令（用于 rollout）"""
    try:
        from .ssh_executor import exec_ssh
        result = exec_ssh(host, command, ssh_user, ssh_password)
        return (
            result.exit_code == 0,
            f"{host}: {'OK' if result.exit_code == 0 else result.stderr}",
            {
                "host": host,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
    except Exception as e:
        return False, f"{host}: {str(e)}", {"host": host, "error": str(e)}


async def _rollout_executor(
    host: str,
    ssh_user: str,
    ssh_password: str,
    steps: list,
) -> tuple[bool, str, list]:
    """Rollout 执行器函数签名（符合 RolloutEngine 期望）"""
    results = []
    all_success = True

    for step in steps:
        cmd = step.get("command", "")
        if not cmd:
            continue

        success, msg, detail = await _run_ssh_for_host(host, ssh_user, ssh_password, cmd)
        results.append(detail)
        if not success:
            all_success = False

    return all_success, f"{host}: {'OK' if all_success else 'FAILED'}", results


async def _run_rollout_async(args: argparse.Namespace):
    """异步执行灰度发布"""
    hosts = args.hosts.split(",") if hasattr(args, "hosts") and args.hosts else []
    if not hosts:
        hosts = args.hosts_list if hasattr(args, "hosts_list") else []

    percents = [int(p.strip()) for p in args.percents.split(",")] if args.percents else [10, 50, 100]
    gate_cfg = _parse_gate_args(args)

    # 构造 RolloutRequest
    req = RolloutRequest(
        command=args.command,
        hosts=hosts,
        percents=percents,
        ssh_user=args.ssh_user,
        ssh_password=args.ssh_password or None,
        gate=SchemaGateConfig(
            kind=GateKind(gate_cfg.kind.value) if gate_cfg else GateKind.CMD,
            url=gate_cfg.url if gate_cfg else None,
            port=gate_cfg.port if gate_cfg else None,
            host=gate_cfg.host if gate_cfg else None,
            process_name=gate_cfg.process_name if gate_cfg else None,
            prom_url=gate_cfg.prom_url if gate_cfg else None,
            prom_query=gate_cfg.prom_query if gate_cfg else None,
            prom_op=gate_cfg.prom_op if gate_cfg else None,
            prom_threshold=gate_cfg.prom_threshold if gate_cfg else None,
            cmd=gate_cfg.cmd if gate_cfg else None,
            timeout_s=gate_cfg.timeout_s if gate_cfg else 5,
        ) if gate_cfg else None,
        dry_run=args.dry_run,
    )

    # 创建引擎
    engine = RolloutEngine(req)

    # Dry-run
    if args.dry_run:
        plan = engine.plan()
        print(json.dumps(plan.model_dump(), ensure_ascii=False, indent=2))
        return

    # 执行
    print(f"🚀 开始灰度发布")
    print(f"   任务: {args.command}")
    print(f"   主机: {len(hosts)} 台")
    print(f"   百分比: {percents}")
    print(f"   Gate: {gate_cfg.kind.value if gate_cfg else '无'}")
    print()

    report = await engine.execute(_rollout_executor)

    # 输出结果
    print()
    print(f"📊 发布完成: {report.success}")
    print(f"   总耗时: {report.total_duration_ms}ms")

    for batch in report.batch_reports:
        status_icon = "✓" if batch.success else "✗"
        print(f"   {status_icon} 批次 {batch.batch_index + 1}: {batch.percent}% - {batch.success_count}/{batch.host_count} 成功")
        if batch.error_message:
            print(f"      错误: {batch.error_message}")


def run_rollout(args: Optional[list] = None):
    """运行灰度发布 CLI 命令"""
    parser = argparse.ArgumentParser(description="灰度发布 CLI")
    subparsers = parser.add_subparsers(dest="subcommand", help="子命令")

    # run-rollout 子命令
    run_parser = subparsers.add_parser("run-rollout", help="执行灰度发布")
    run_parser.add_argument("--command", "-c", required=True, help="运维指令")
    run_parser.add_argument("--hosts", help="目标主机 (逗号分隔)")
    run_parser.add_argument("--percents", default="10,50,100", help="灰度百分比 (逗号分隔)")
    run_parser.add_argument("--ssh-user", default="root", help="SSH 用户")
    run_parser.add_argument("--ssh-password", default="", help="SSH 密码")
    run_parser.add_argument("--gate-http", metavar="URL", help="HTTP Gate 检查 URL")
    run_parser.add_argument("--gate-port", metavar="HOST:PORT", help="端口 Gate 检查")
    run_parser.add_argument("--gate-process", metavar="NAME", help="进程 Gate 检查")
    run_parser.add_argument("--gate-promql", metavar="URL|QUERY|OP|THRESHOLD", help="PromQL Gate 检查")
    run_parser.add_argument("--gate-cmd", metavar="CMD", help="自定义命令 Gate")
    run_parser.add_argument("--dry-run", action="store_true", help="仅预览计划")
    run_parser.set_defaults(func=lambda a: asyncio.run(_run_rollout_async(a)))

    # list-rollouts 子命令
    list_parser = subparsers.add_parser("list-rollouts", help="列出运行中的发布")

    # status-rollout 子命令
    status_parser = subparsers.add_parser("status-rollout", help="查看发布状态")
    status_parser.add_argument("rollout_id", help="发布 ID")

    # cancel-rollout 子命令
    cancel_parser = subparsers.add_parser("cancel-rollout", help="取消发布")
    cancel_parser.add_argument("rollout_id", help="发布 ID")

    # rollback-rollout 子命令
    rollback_parser = subparsers.add_parser("rollback-rollout", help="回滚发布")
    rollback_parser.add_argument("rollout_id", help="发布 ID")

    parsed = parser.parse_args(args)

    if not parsed.subcommand:
        parser.print_help()
        return

    if hasattr(parsed, "func"):
        parsed.func(parsed)
    else:
        # 内置子命令
        if parsed.subcommand == "list-rollouts":
            from api.routes.rollout import ROLLOUT_STORE
            sessions = list(ROLLOUT_STORE.values())
            if not sessions:
                print("暂无运行中的发布")
                return
            for s in sessions:
                print(f"  {s.id} | {s.status} | {s.task_text[:50]} | {len(s.hosts)}台 | {len(s.batches)}批")
        elif parsed.subcommand == "status-rollout":
            from api.routes.rollout import ROLLOUT_STORE
            if parsed.rollout_id not in ROLLOUT_STORE:
                print(f"发布 {parsed.rollout_id} 不存在")
                sys.exit(1)
            s = ROLLOUT_STORE[parsed.rollout_id]
            print(json.dumps(s.to_status_response().model_dump(), ensure_ascii=False, indent=2))
        elif parsed.subcommand == "cancel-rollout":
            import requests
            r = requests.post(f"http://localhost:8000/api/v1/rollout/{parsed.rollout_id}/cancel")
            print(r.json())
        elif parsed.subcommand == "rollback-rollout":
            import requests
            r = requests.post(f"http://localhost:8000/api/v1/rollout/{parsed.rollout_id}/rollback")
            print(r.json())


# ─── 便捷入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_rollout(sys.argv[1:])
