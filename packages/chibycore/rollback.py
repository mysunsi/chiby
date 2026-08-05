"""回滚引擎：执行失败时自动撤销操作。"""
from __future__ import annotations

import logging
from typing import List, Optional

from .schemas import ExecutionStep, StepStatus
from .script_generator import build_rollback_command
from .ssh_executor import CmdResult, exec_local, exec_ssh

logger = logging.getLogger(__name__)


def rollback_step(
    step: ExecutionStep,
    params: dict,
    host: str,
    user: str,
    password: str,
) -> CmdResult:
    """对单个步骤执行回滚。"""
    rollback_cmd = step.rollback_command or build_rollback_command(step.action, params)
    if not rollback_cmd:
        return CmdResult(
            stdout="", stderr="无回滚命令",
            exit_code=-1, duration_ms=0, success=False,
        )

    logger.info(f"执行回滚: {rollback_cmd}")

    if host == "127.0.0.1" or host == "localhost":
        return exec_local(rollback_cmd)
    else:
        return exec_ssh(host, rollback_cmd, user, password)


def rollback_failed_steps(
    completed_steps: List[ExecutionStep],
    params: dict,
    host: str,
    user: str,
    password: str,
) -> List[ExecutionStep]:
    """从后往前回滚所有已完成的失败步骤。"""
    rollback_log: List[ExecutionStep] = []

    # 只回滚成功的步骤（失败的无法再回滚了）
    to_rollback = [
        s for s in reversed(completed_steps)
        if s.status in (StepStatus.SUCCESS, StepStatus.VERIFIED)
    ]

    for step in to_rollback:
        if not step.rollback_command:
            continue

        rb = rollback_step(step, params, host, user, password)
        rb_step = ExecutionStep(
            action=step.action,
            description=f"🔄 回滚: {step.description}",
            command=step.rollback_command,
            status=StepStatus.ROLLED_BACK if rb.success else StepStatus.FAILED,
            stdout=rb.stdout,
            stderr=rb.stderr,
            exit_code=rb.exit_code,
            verified=rb.success,
            verification_output=rb.stdout,
        )
        rollback_log.append(rb_step)
        logger.info(f"回滚结果: {'成功' if rb.success else '失败'} - {rb.stdout[:100]}")

    return rollback_log
