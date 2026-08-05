"""运维助手 API 路由。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from chibycore.chains import ChainPlanner, TASK_CHAINS
from chibycore.engine import run_task
from chibycore.parser import describe_action, parse_command
from chibycore.schemas import ActionType, TaskRequest

router = APIRouter(prefix="/api/v1/ops", tags=["运维助手"])

# ─── 请求/响应模型 ────────────────────────────────────────────────────────────

class ExecuteRequest(BaseModel):
    command: str = Field(..., description="自然语言运维指令")
    host: str = Field(default="127.0.0.1")
    ssh_user: str = Field(default="sunsi")
    ssh_password: Optional[str] = Field(
        default=None,
        description="SSH 密码（留空使用配置中的默认密码）",
    )
    dry_run: bool = Field(default=False, description="仅预览计划，不实际执行")


class StepDetail(BaseModel):
    step_id: str
    action: str
    description: str
    command: str
    depends_on: List[str]
    parallel_group: Optional[str]
    status: str = "pending"
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: int = 0
    verified: bool = False


class ExecuteResponse(BaseModel):
    task_id: str
    status: str
    original_command: str
    chain_name: Optional[str] = None
    chain_id: Optional[str] = None
    steps: List[StepDetail]
    final_output: str = ""
    error_message: Optional[str] = None
    total_duration_ms: int = 0
    created_at: str


class ChainInfo(BaseModel):
    chain_id: str
    name: str
    description: str
    step_count: int
    requires_approval: bool
    keywords: List[str]
    steps_summary: List[str]


class PreviewResponse(BaseModel):
    chain_id: str
    chain_name: str
    matched_keywords: List[str]
    params: Dict[str, Any]
    plan: List[Dict[str, Any]]


class TaskHistoryItem(BaseModel):
    task_id: str
    command: str
    task_type: str
    chain_name: Optional[str]
    action: Optional[str]
    status: str
    steps_count: int
    duration_ms: int
    created_at: str
    finished_at: Optional[str]


class TaskHistoryDetail(BaseModel):
    task_id: str
    command: str
    task_type: str
    chain_name: Optional[str]
    action: Optional[str]
    params: Dict[str, Any]
    status: str
    steps_count: int
    steps_detail: List[Dict[str, Any]]
    final_output: Optional[str]
    error_message: Optional[str]
    duration_ms: int
    created_at: str
    finished_at: Optional[str]


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

def _to_iso(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


# ─── 端点实现 ─────────────────────────────────────────────────────────────────

@router.get("/chains", response_model=List[ChainInfo])
async def list_chains():
    """列出所有可用任务链。"""
    result = []
    planner = ChainPlanner()
    for cid, chain in TASK_CHAINS.items():
        plan = planner.build_plan(chain, {})
        result.append(ChainInfo(
            chain_id=cid,
            name=chain.name,
            description=chain.description,
            step_count=len(chain.steps),
            requires_approval=chain.requires_approval,
            keywords=chain.intent_keywords[:5],
            steps_summary=[
                f"{i+1}. {s.description}" for i, s in enumerate(chain.steps)
            ],
        ))
    return result


@router.post("/preview", response_model=PreviewResponse)
async def preview_command(req: ExecuteRequest):
    """预览任务链执行计划（dry_run 模式）。"""
    planner = ChainPlanner()
    chain, shared_params = planner.match_chain(req.command)

    if chain is None:
        action, params = parse_command(req.command)
        desc = describe_action(action, params)
        plan = [{
            "step_id": "step_0",
            "action": action.value,
            "description": desc,
            "depends_on": [],
            "parallel_group": None,
            "params": params,
        }]
        return PreviewResponse(
            chain_id="single_step",
            chain_name=f"单步执行: {action.value}",
            matched_keywords=[],
            params=params,
            plan=plan,
        )

    plan = planner.build_plan(chain, shared_params)
    return PreviewResponse(
        chain_id=next(cid for cid, c in TASK_CHAINS.items() if c is chain),
        chain_name=chain.name,
        matched_keywords=[kw for kw in chain.intent_keywords if kw in req.command.lower()],
        params=shared_params,
        plan=[
            {
                "step_id": p["step_id"],
                "action": p["action"].value,
                "description": p["description"],
                "depends_on": p["depends_on"],
                "parallel_group": p["parallel_group"],
            }
            for p in plan
        ],
    )


@router.post("/execute", response_model=ExecuteResponse)
async def execute_ops_command(req: ExecuteRequest):
    """自然语言运维命令 → Celery 异步分发 → 同步返回完整结果。

    本地模式（ALWAYS_EAGER=True）：Celery 同步执行，立即返回。
    生产模式：任务入队，立即返回 task_id，轮询 /api/v1/ops/tasks/{celery_id} 获取结果。
    """
    t0 = time.time()
    task_id = str(uuid.uuid4())[:8]

    # ── Dry-run ────────────────────────────────────────────────────────────────
    if req.dry_run:
        planner = ChainPlanner()
        chain, shared_params = planner.match_chain(req.command)
        plan = planner.build_plan(chain, shared_params) if chain else []
        chain_id = next((cid for cid, c in TASK_CHAINS.items() if c is chain), "single_step")
        return ExecuteResponse(
            task_id=task_id,
            status="planned",
            original_command=req.command,
            chain_name=chain.name if chain else "单步执行",
            chain_id=chain_id,
            steps=[
                StepDetail(
                    step_id=p["step_id"],
                    action=p["action"].value,
                    description=p["description"],
                    command="(dry_run) " + p["description"],
                    depends_on=p["depends_on"],
                    parallel_group=p.get("parallel_group"),
                )
                for p in plan
            ],
            final_output="",
            total_duration_ms=0,
            created_at=datetime.utcnow().isoformat(),
        )

    # ── Celery 分发 ──────────────────────────────────────────────────────────
    from chibycore.tasks import execute_ops_task
    from chibycore.chains import ChainPlanner as CP2
    import os

    password = req.ssh_password or "csswzqzy"
    is_local = os.getenv("OPS_CELERY_LOCAL", "true").lower() == "true"

    if is_local:
        # 本地模式：apply() 同步执行
        celery_result = execute_ops_task.apply(
            args=[req.command, req.host, req.ssh_user, password],
        )
        celery_data = celery_result.result
        task_id = celery_data.get("task_id", task_id)
    else:
        # 生产模式：apply_async 入队
        celery_result = execute_ops_task.apply_async(
            args=[req.command, req.host, req.ssh_user, password],
            task_id=task_id,
        )
        if celery_result.ready():
            celery_data = celery_result.get(timeout=5)
        else:
            return ExecuteResponse(
                task_id=task_id,
                status="pending",
                original_command=req.command,
                chain_name=None,
                chain_id=None,
                steps=[],
                final_output="",
                error_message=None,
                total_duration_ms=0,
                created_at=datetime.utcnow().isoformat(),
            )

    # ── 构造返回 ────────────────────────────────────────────────────────────
    try:
        chain_name = celery_data.get("chain_name")
        planner2 = CP2()
        chain2, _ = planner2.match_chain(req.command)

        return ExecuteResponse(
            task_id=task_id,
            status=celery_data.get("status", "success"),
            original_command=req.command,
            chain_name=chain2.name if chain2 else chain_name,
            chain_id=next((cid for cid, c in TASK_CHAINS.items() if c is chain2), "single_step"),
            steps=[
                StepDetail(
                    step_id="step_0",
                    action="task_result",
                    description="任务结果",
                    command="",
                    depends_on=[],
                    parallel_group=None,
                    status=celery_data.get("status", "success"),
                    stdout=celery_data.get("output", ""),
                    exit_code=0 if celery_data.get("status") == "success" else 1,
                    duration_ms=celery_data.get("duration_ms", 0),
                )
            ],
            final_output=celery_data.get("output", ""),
            error_message=celery_data.get("error"),
            total_duration_ms=celery_data.get("duration_ms", 0),
            created_at=datetime.utcnow().isoformat(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"任务执行失败: {e}")


@router.get("/tasks/{celery_task_id}")
async def get_celery_task_status(celery_task_id: str) -> dict:
    """轮询 Celery 任务状态（生产模式）。"""
    from celery.result import AsyncResult
    from chibycore.celery_config import celery_app

    try:
        async_result = AsyncResult(celery_task_id, app=celery_app)
        return {
            "celery_task_id": celery_task_id,
            "status": async_result.status,
            "ready": async_result.ready(),
            "result": async_result.result if async_result.ready() else None,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 历史查询 ─────────────────────────────────────────────────────────────────

@router.get("/history")
async def list_history(
    limit: int = 20,
    offset: int = 0,
    chain_name: Optional[str] = None,
    status: Optional[str] = None,
) -> dict:
    """查询任务执行历史（分页，支持过滤）。"""
    try:
        from chibycore import database as _db
        records, total = _db.get_task_history(
            limit=limit,
            offset=offset,
            chain_name=chain_name,
            status_filter=status,
        )
        items = []
        for r in records:
            items.append(TaskHistoryItem(
                task_id=r.task_id,
                command=r.command[:200],
                task_type=r.task_type,
                chain_name=r.chain_name,
                action=r.action,
                status=r.status,
                steps_count=r.steps_count or 0,
                duration_ms=r.duration_ms or 0,
                created_at=_to_iso(r.created_at) or "",
                finished_at=_to_iso(r.finished_at),
            ).model_dump())
        return {"total": total, "limit": limit, "offset": offset, "tasks": items}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"历史查询失败: {e}")


@router.get("/history/{task_id}", response_model=TaskHistoryDetail)
async def get_history_detail(task_id: str) -> TaskHistoryDetail:
    """查询单个任务详情。"""
    try:
        from chibycore import database as _db
        record = _db.get_task_by_id(task_id)
        if not record:
            raise HTTPException(status_code=404, detail="任务不存在")
        return TaskHistoryDetail(
            task_id=record.task_id,
            command=record.command,
            task_type=record.task_type,
            chain_name=record.chain_name,
            action=record.action,
            params=record.params or {},
            status=record.status,
            steps_count=record.steps_count or 0,
            steps_detail=record.steps_detail or [],
            final_output=record.final_output,
            error_message=record.error_message,
            duration_ms=record.duration_ms or 0,
            created_at=_to_iso(record.created_at) or "",
            finished_at=_to_iso(record.finished_at),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")


@router.get("/health")
async def health():
    return {"status": "ok", "service": "ops-assistant"}
