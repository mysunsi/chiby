"""Ops 引擎：编排 Parser → ScriptGenerator → Executor → Validator → Rollback。

支持两种执行模式：
  1. 任务链模式（TaskChain）：复杂操作拆分为多步工作流
  2. 单步模式（fallback）：直接执行单个动作

执行结果自动持久化到数据库。
Phase 2: LLM 编排（当可用时）自动路由 + 失败分析重试。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .chains import ChainExecutor, ChainPlanner, TaskChain, TASK_CHAINS
from .config import EXECUTION_MODE, MAX_RETRIES
from .llm_providers import get_llm
from .parser import describe_action, parse_command
from .schemas import ActionType, ExecutionStep, StepStatus, TaskRequest, TaskResponse
from .script_generator import build_command, build_verify_command, build_rollback_command
from .ssh_executor import CmdResult, exec_local, exec_ssh
from .validator import format_resource_report, validate

logger = logging.getLogger(__name__)

# ── 数据库初始化 ────────────────────────────────────────────────────────────────
try:
    from . import database as _db
    _db.init_db()
    logger.info("数据库连接就绪")
    _DB_OK = True
except Exception as e:
    logger.warning(f"数据库初始化失败，将跳过持久化: {e}")
    _DB_OK = False


def _save_history(
    task_id: str,
    command: str,
    task_type: str,
    status: str,
    chain_name: Optional[str] = None,
    action: Optional[str] = None,
    params: Optional[dict] = None,
    steps: Optional[list] = None,
    final_output: Optional[str] = None,
    error_message: Optional[str] = None,
    duration_ms: int = 0,
) -> None:
    """持久化任务记录（失败不影响主流程）。"""
    if not _DB_OK:
        return
    try:
        _db.save_task_history(
            task_id=task_id,
            command=command,
            task_type=task_type,
            chain_name=chain_name,
            action=action,
            params=params,
            steps=steps,
            final_output=final_output,
            error_message=error_message,
            duration_ms=duration_ms,
            status=status,
        )
    except Exception as e:
        logger.warning(f"记录保存失败（不影响执行）: {e}")


# ─── 单步执行（内部用）────────────────────────────────────────────────────────
def _run_step(
    step: ExecutionStep,
    host: str,
    user: str,
    password: str,
) -> ExecutionStep:
    """执行单个步骤：命令 → 验证 → 状态更新。"""
    t0 = time.time()
    step.status = StepStatus.RUNNING
    step.executed_at = datetime.utcnow()

    if host in ("127.0.0.1", "localhost") or EXECUTION_MODE == "local":
        result: CmdResult = exec_local(step.command)
    else:
        result = exec_ssh(host, step.command, user, password)

    step.stdout = result.stdout
    step.stderr = result.stderr
    step.exit_code = result.exit_code
    step.duration_ms = int((time.time() - t0) * 1000)

    if result.exit_code == 0:
        step.status = StepStatus.SUCCESS
    else:
        step.status = StepStatus.FAILED
        logger.warning(f"步骤失败: {step.description} | exit={result.exit_code} | {result.stderr[:100]}")
        return step

    # 自动验证
    if step.verify_command:
        t0v = time.time()
        verify_result: CmdResult = (
            exec_local(step.verify_command)
            if host in ("127.0.0.1", "localhost")
            else exec_ssh(host, step.verify_command, user, password)
        )
        step.verification_output = verify_result.stdout
        step.duration_ms += int((time.time() - t0v) * 1000)
        step.verified = verify_result.exit_code == 0
        step.status = StepStatus.VERIFIED if step.verified else StepStatus.FAILED

    return step


# ─── 主入口 ───────────────────────────────────────────────────────────────────
def run_task(req: TaskRequest) -> TaskResponse:
    """主入口：匹配任务链 or 解析单步 → 执行 → 返回结果。"""
    task_id = str(uuid.uuid4())[:8]
    t0 = time.time()
    host = req.host or "127.0.0.1"
    user = req.ssh_user or "sunsi"
    password = req.ssh_password or "csswzqzy"

    logger.info(f"[{task_id}] 新任务: {req.command}")

    # ── 1. 匹配任务链 ────────────────────────────────────────────────────────
    planner = ChainPlanner()
    chain, shared_params = planner.match_chain(req.command)

    if chain is not None:
        return _run_chain(
            task_id=task_id,
            chain=chain,
            shared_params=shared_params,
            host=host,
            user=user,
            password=password,
            t0=t0,
            original_command=req.command,
        )

    # ── 2. Fallback：单步解析执行 ────────────────────────────────────────────
    return _run_single(
        task_id=task_id,
        req=req,
        host=host,
        user=user,
        password=password,
        t0=t0,
    )


# ─── 任务链执行路径 ────────────────────────────────────────────────────────────
def _run_chain(
    task_id: str,
    chain: TaskChain,
    shared_params: Dict,
    host: str,
    user: str,
    password: str,
    t0: float,
    original_command: str,
) -> TaskResponse:
    """执行任务链。"""
    logger.info(f"[{task_id}] 使用任务链: {chain.name}（{len(chain.steps)} 个步骤）")

    plan = ChainPlanner().build_plan(chain, shared_params)
    executor = ChainExecutor(host, user, password)
    chain_result = executor.execute_plan(plan, dry_run=False)

    # 转换链结果为 TaskResponse
    all_steps = []
    final_output_lines = []
    final_status = chain_result["chain_status"]

    for r in chain_result["all_results"]:
        step = ExecutionStep(
            action=ActionType(r["action"]),
            description=r["description"],
            command=r["command"],
            status=StepStatus(r["status"]),
            stdout=r["stdout"],
            stderr=r["stderr"],
            exit_code=r["exit_code"],
            verified=r.get("verified", False),
            verification_output=r.get("verification_output", ""),
            duration_ms=r.get("duration_ms", 0),
        )
        all_steps.append(step)
        if r["stdout"]:
            final_output_lines.append(f"=== {r['description']} ===\n{r['stdout'][:300]}")

    total_ms = int((time.time() - t0) * 1000)
    final_output = "\n".join(final_output_lines)

    # 持久化
    _save_history(
        task_id=task_id,
        command=original_command,
        task_type="chain",
        status=final_status,
        chain_name=chain.name,
        action=chain_result["all_results"][0]["action"] if chain_result["all_results"] else None,
        params=shared_params,
        steps=chain_result["all_results"],
        final_output=final_output,
        duration_ms=total_ms,
    )

    return TaskResponse(
        task_id=task_id,
        status=final_status,
        original_command=original_command,
        parsed_action=ActionType(chain_result["all_results"][0]["action"])
            if chain_result["all_results"] else ActionType.UNKNOWN,
        parsed_params=shared_params,
        steps=all_steps,
        final_output=final_output[:3000],
        error_message=None,
        total_duration_ms=total_ms,
        created_at=datetime.utcnow().isoformat(),
    )


# ─── 单步执行路径（原有逻辑）───────────────────────────────────────────────────
def _run_single(
    task_id: str,
    req: TaskRequest,
    host: str,
    user: str,
    password: str,
    t0: float,
) -> TaskResponse:
    """原有单步解析执行逻辑。"""
    action, params = parse_command(req.command)
    action_desc = describe_action(action, params)
    logger.info(f"[{task_id}] 单步动作: {action.value} | 参数: {params}")

    main_cmd = build_command(action, params, password)
    verify_cmd = build_verify_command(action, params)

    main_step = ExecutionStep(
        action=action,
        description=action_desc,
        command=main_cmd,
        verify_command=verify_cmd,
        rollback_command=None,
        status=StepStatus.PENDING,
    )

    final_status = "success"
    error_message: Optional[str] = None
    all_steps = [main_step]

    for attempt in range(MAX_RETRIES + 1):
        if attempt > 0:
            logger.info(f"[{task_id}] 重试 #{attempt}")
            time.sleep(1)

        main_step = _run_step(main_step, host, user, password)
        all_steps = [main_step]

        if main_step.status in (StepStatus.SUCCESS, StepStatus.VERIFIED):
            break
        else:
            final_status = "failed"
            error_message = main_step.stderr or "命令执行失败"
            if attempt == MAX_RETRIES:
                rollback_cmd = build_rollback_command(action, params)
                if rollback_cmd:
                    rb_step = ExecutionStep(
                        action=action,
                        description=f"🔄 回滚: {action_desc}",
                        command=rollback_cmd,
                        status=StepStatus.RUNNING,
                    )
                    rb_step = _run_step(rb_step, host, user, password)
                    all_steps.append(rb_step)
                final_status = "failed"
                break
            error_message = f"重试 {attempt + 1}/{MAX_RETRIES + 1} 后仍失败"

    total_ms = int((time.time() - t0) * 1000)
    final_output = main_step.stdout or main_step.stderr or ""

    if action in (
        ActionType.DISK_USAGE, ActionType.MEMORY_USAGE,
        ActionType.CPU_USAGE, ActionType.PROCESS_LIST,
    ):
        final_output = format_resource_report(main_step.stdout, action)

    if main_step.status == StepStatus.VERIFIED:
        final_status = "success"

    # 持久化
    _save_history(
        task_id=task_id,
        command=req.command,
        task_type="single",
        status=final_status,
        action=action.value,
        params=params,
        steps=[{
            "action": s.action.value,
            "description": s.description,
            "status": s.status.value,
            "exit_code": s.exit_code,
            "verified": s.verified,
            "duration_ms": s.duration_ms,
            "stdout": (s.stdout or "")[:500],
        } for s in all_steps],
        final_output=final_output[:3000],
        error_message=error_message,
        duration_ms=total_ms,
    )

    # ── LLM 调试循环（失败时调用 LLM 分析 + 修正重试）────────────────────────
    llm = get_llm()
    if final_status == "failed" and llm.is_available and llm._active:
        try:
            # 异步 LLM 分析（同步调用）
            import asyncio
            loop = asyncio.new_event_loop()
            analysis = loop.run_until_complete(
                _llm_analyze_failure(
                    failed_command=main_cmd,
                    stderr=main_step.stderr or "",
                    exit_code=main_step.exit_code or -1,
                )
            )
            loop.close()

            if analysis:
                logger.info(f"[{task_id}] LLM分析: {analysis.get('analysis','')}")
                # 将 LLM 分析附加到 error_message
                error_message = (
                    f"{error_message}\n\n[LLM 分析] {analysis.get('analysis','')}\n"
                    f"[建议] {analysis.get('suggestion','')}"
                )
        except Exception as e:
            logger.warning(f"LLM 分析调用失败: {e}")

    return TaskResponse(
        task_id=task_id,
        status=final_status,
        original_command=req.command,
        parsed_action=action,
        parsed_params=params,
        steps=all_steps,
        final_output=final_output,
        error_message=error_message,
        total_duration_ms=total_ms,
        created_at=datetime.utcnow().isoformat(),
    )


# ─── LLM 失败分析（异步）─────────────────────────────────────────────────────────

async def _llm_analyze_failure(
    failed_command: str,
    stderr: str,
    exit_code: int,
) -> Optional[Dict[str, Any]]:
    """异步调用 LLM 分析执行失败原因。"""
    from .llm_orchestrator import analyze_failure
    return await analyze_failure(failed_command, stderr, exit_code)
