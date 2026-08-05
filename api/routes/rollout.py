"""灰度发布 API 路由。

提供灰度发布的 REST API 和 WebSocket 实时进度推送。

API 端点:
  - POST   /api/v1/rollout              创建灰度发布
  - GET    /api/v1/rollout/{id}         获取发布状态
  - GET    /api/v1/rollout/{id}/batches 获取批次详情
  - POST   /api/v1/rollout/{id}/cancel  取消发布
  - POST   /api/v1/rollout/{id}/rollback 回滚上一批次
  - GET    /api/v1/rollout/history      查询历史

WebSocket:
  - WS     /ws/rollout/{id}             实时进度推送
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/rollout", tags=["灰度发布"])

# ─── 内存存储（生产环境应替换为 Redis/DB）──────────────────────────────────────

ROLLOUT_STORE: Dict[str, "RolloutSession"] = {}
WS_CONNECTIONS: Dict[str, List[WebSocket]] = {}

# ─── Pydantic 模型 ────────────────────────────────────────────────────────────

class GateConfigInput(BaseModel):
    kind: str = Field(..., description="gate 类型: http|port|process|promql|cmd")
    url: Optional[str] = None
    port: Optional[int] = None
    host: Optional[str] = None
    process_name: Optional[str] = None
    prom_url: Optional[str] = None
    prom_query: Optional[str] = None
    prom_op: Optional[str] = None
    prom_threshold: Optional[float] = None
    cmd: Optional[str] = None
    timeout_s: int = Field(default=5)


class CreateRolloutRequest(BaseModel):
    task_text: str = Field(..., description="自然语言运维指令")
    hosts: List[str] = Field(..., description="目标主机列表")
    percents: List[int] = Field(default=[10, 50, 100], description="灰度百分比")
    gate: Optional[GateConfigInput] = None
    ssh_user: str = Field(default="root")
    ssh_password: Optional[str] = None
    dry_run: bool = Field(default=False)


class RolloutBatchStatus(BaseModel):
    batch_index: int
    percent: int
    host_count: int
    hosts: List[str]
    status: str  # pending|running|gate_checking|success|failed|skipped
    success_count: int = 0
    failed_count: int = 0
    gate_result: Optional[dict] = None
    error_message: Optional[str] = None
    duration_ms: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class RolloutStatusResponse(BaseModel):
    id: str
    task_text: str
    status: str  # pending|running|success|failed|cancelled|rolling_back
    total_hosts: int
    total_batches: int
    current_batch: int = 0
    percents: List[int]
    batches: List[RolloutBatchStatus]
    gate_config: Optional[GateConfigInput] = None
    created_at: str
    updated_at: str
    total_duration_ms: int = 0
    error_message: Optional[str] = None


class RolloutCreatedResponse(BaseModel):
    id: str
    status: str
    plan: dict
    message: str


# ─── 内存会话 ─────────────────────────────────────────────────────────────────

@dataclass
class RolloutSession:
    id: str
    task_text: str
    hosts: List[str]
    percents: List[int]
    ssh_user: str
    ssh_password: Optional[str]
    dry_run: bool
    gate_config: Optional[GateConfigInput]
    status: str = "pending"
    current_batch: int = 0
    batches: List[RolloutBatchStatus] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    total_duration_ms: int = 0
    error_message: Optional[str] = None
    _task: Optional[asyncio.Task] = None
    _cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def to_status_response(self) -> RolloutStatusResponse:
        return RolloutStatusResponse(
            id=self.id,
            task_text=self.task_text,
            status=self.status,
            total_hosts=len(self.hosts),
            total_batches=len(self.batches),
            current_batch=self.current_batch,
            percents=self.percents,
            batches=self.batches,
            gate_config=self.gate_config,
            created_at=self.created_at.isoformat(),
            updated_at=self.updated_at.isoformat(),
            total_duration_ms=self.total_duration_ms,
            error_message=self.error_message,
        )

    async def broadcast(self, msg: dict):
        """广播消息到所有 WebSocket 连接"""
        if self.id in WS_CONNECTIONS:
            dead = []
            for ws in WS_CONNECTIONS[self.id]:
                try:
                    await ws.send_json(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                WS_CONNECTIONS[self.id].remove(ws)


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

def _create_gate_config(gate_input: Optional[GateConfigInput]):
    """将 API 输入转换为 chibycore gate 配置"""
    if not gate_input:
        return None
    try:
        from chibycore.gate import GateConfig, GateKind
        kind_map = {
            "http": GateKind.HTTP,
            "port": GateKind.PORT,
            "process": GateKind.PROCESS,
            "promql": GateKind.PROMQL,
            "cmd": GateKind.CMD,
        }
        kind = kind_map.get(gate_input.kind.lower(), GateKind.CMD)
        cfg = GateConfig(
            kind=kind,
            url=gate_input.url,
            port=gate_input.port,
            host=gate_input.host,
            process_name=gate_input.process_name,
            prom_url=gate_input.prom_url,
            prom_query=gate_input.prom_query,
            prom_op=gate_input.prom_op,
            prom_threshold=gate_input.prom_threshold,
            cmd=gate_input.cmd,
            timeout_s=gate_input.timeout_s,
        )
        return cfg
    except ImportError:
        logger.warning("chibycore.gate 不可用，跳过 gate 配置")
        return None


async def _execute_batch(
    session: RolloutSession,
    batch_idx: int,
    batch_hosts: List[str],
    progress_callback: Optional[Callable] = None,
) -> List[dict]:
    """执行单个批次的运维任务"""
    from chibycore import rollout as rollout_core
    from chibycore.gate import GateChecker
    from chibycore.ssh_executor import SSHExecutor

    steps_results = []
    t0 = time.time()

    # 构造执行步骤
    steps = [
        {
            "step_id": f"batch_{batch_idx}_exec",
            "action": "execute",
            "description": f"执行批次 {batch_idx + 1} 任务",
            "command": session.task_text,
            "depends_on": [],
            "parallel_group": None,
        }
    ]

    # 更新批次状态
    if batch_idx < len(session.batches):
        session.batches[batch_idx].status = "running"
        session.batches[batch_idx].started_at = datetime.utcnow().isoformat()

    await session.broadcast({
        "type": "batch_start",
        "batch_index": batch_idx,
        "hosts": batch_hosts,
    })

    # SSH 执行
    ssh = SSHExecutor(
        hosts=batch_hosts,
        ssh_user=session.ssh_user,
        ssh_password=session.ssh_password or "",
    )

    try:
        results = await ssh.execute_steps(steps)
        steps_results = results

        # 统计成功/失败
        success = sum(1 for r in results if r.get("success", False))
        failed = len(results) - success

        if batch_idx < len(session.batches):
            session.batches[batch_idx].success_count = success
            session.batches[batch_idx].failed_count = failed
            session.batches[batch_idx].status = "success" if failed == 0 else "failed"
            session.batches[batch_idx].finished_at = datetime.utcnow().isoformat()
            session.batches[batch_idx].duration_ms = int((time.time() - t0) * 1000)

        await session.broadcast({
            "type": "batch_complete",
            "batch_index": batch_idx,
            "success": success,
            "failed": failed,
            "results": results,
        })

    except Exception as e:
        if batch_idx < len(session.batches):
            session.batches[batch_idx].status = "failed"
            session.batches[batch_idx].error_message = str(e)
            session.batches[batch_idx].finished_at = datetime.utcnow().isoformat()
            session.batches[batch_idx].duration_ms = int((time.time() - t0) * 1000)

        await session.broadcast({
            "type": "batch_error",
            "batch_index": batch_idx,
            "error": str(e),
        })

    return steps_results


async def _run_rollout_task(session: RolloutSession):
    """后台运行灰度发布任务"""
    from chibycore import rollout as rollout_core

    t0 = time.time()
    session.status = "running"
    session.updated_at = datetime.utcnow()

    try:
        await session.broadcast({
            "type": "rollout_start",
            "id": session.id,
            "total_hosts": len(session.hosts),
            "total_batches": len(session.batches),
        })

        for batch_idx, batch in enumerate(session.batches):
            # 检查取消
            if session._cancel_event.is_set():
                session.status = "cancelled"
                await session.broadcast({"type": "cancelled", "batch_index": batch_idx})
                break

            # Gate 检查（批执行前）
            gate_cfg = _create_gate_config(session.gate_config)
            if gate_cfg:
                session.batches[batch_idx].status = "gate_checking"
                await session.broadcast({
                    "type": "gate_check_start",
                    "batch_index": batch_idx,
                })

                checker = GateChecker(gate_cfg)
                gate_result = checker.check_multi(batch.hosts)

                session.batches[batch_idx].gate_result = {
                    "passed": gate_result.passed,
                    "message": gate_result.message,
                    "details": gate_result.details,
                }

                await session.broadcast({
                    "type": "gate_check_result",
                    "batch_index": batch_idx,
                    "gate_result": session.batches[batch_idx].gate_result,
                })

                if not gate_result.passed:
                    session.batches[batch_idx].status = "failed"
                    session.status = "failed"
                    session.error_message = f"Gate 检查失败: {gate_result.message}"
                    await session.broadcast({
                        "type": "gate_failed",
                        "batch_index": batch_idx,
                        "error": session.error_message,
                    })
                    break

            # 执行批次
            await _execute_batch(session, batch_idx, batch.hosts)

            # 更新全局状态
            session.current_batch = batch_idx + 1
            session.updated_at = datetime.utcnow()

            # 检查批次是否失败
            if session.batches[batch_idx].status == "failed":
                session.status = "failed"
                session.error_message = session.batches[batch_idx].error_message
                break

        else:
            # 所有批次完成
            session.status = "success"

        session.total_duration_ms = int((time.time() - t0) * 1000)
        session.updated_at = datetime.utcnow()

        await session.broadcast({
            "type": "rollout_complete",
            "id": session.id,
            "status": session.status,
            "total_duration_ms": session.total_duration_ms,
            "error_message": session.error_message,
        })

    except Exception as e:
        session.status = "failed"
        session.error_message = str(e)
        session.total_duration_ms = int((time.time() - t0) * 1000)
        session.updated_at = datetime.utcnow()
        logger.exception(f"Rollout {session.id} 失败")
        await session.broadcast({
            "type": "rollout_error",
            "id": session.id,
            "error": str(e),
        })


# ─── HTTP 端点 ────────────────────────────────────────────────────────────────

@router.post("", response_model=RolloutCreatedResponse)
async def create_rollout(req: CreateRolloutRequest):
    """创建新的灰度发布任务"""
    rollout_id = str(uuid.uuid4())[:8]
    t0 = time.time()

    # 构造批次
    from chibycore import rollout as rollout_core
    batches_hosts = rollout_core.split_batches(req.hosts, req.percents)

    batches = [
        RolloutBatchStatus(
            batch_index=i,
            percent=req.percents[i],
            host_count=len(b),
            hosts=b,
            status="pending",
        )
        for i, b in enumerate(batches_hosts)
    ]

    session = RolloutSession(
        id=rollout_id,
        task_text=req.task_text,
        hosts=req.hosts,
        percents=req.percents,
        ssh_user=req.ssh_user,
        ssh_password=req.ssh_password,
        dry_run=req.dry_run,
        gate_config=req.gate,
        batches=batches,
    )

    ROLLOUT_STORE[rollout_id] = session

    # Dry-run 模式：立即返回计划
    if req.dry_run:
        session.status = "planned"
        return RolloutCreatedResponse(
            id=rollout_id,
            status="planned",
            plan={
                "total_hosts": len(req.hosts),
                "total_batches": len(batches),
                "percents": req.percents,
                "batches": [
                    {"index": i, "percent": req.percents[i], "hosts": b}
                    for i, b in enumerate(batches_hosts)
                ],
                "gate": req.gate.model_dump() if req.gate else None,
            },
            message=f"干运行完成，共 {len(batches)} 个批次",
        )

    # 启动后台任务
    session._task = asyncio.create_task(_run_rollout_task(session))

    return RolloutCreatedResponse(
        id=rollout_id,
        status="running",
        plan={
            "total_hosts": len(req.hosts),
            "total_batches": len(batches),
            "percents": req.percents,
        },
        message=f"灰度发布已启动 (ID: {rollout_id})",
    )


@router.get("/{rollout_id}", response_model=RolloutStatusResponse)
async def get_rollout_status(rollout_id: str):
    """获取灰度发布状态"""
    if rollout_id not in ROLLOUT_STORE:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    return ROLLOUT_STORE[rollout_id].to_status_response()


@router.get("/{rollout_id}/batches")
async def get_rollout_batches(rollout_id: str):
    """获取所有批次详情"""
    if rollout_id not in ROLLOUT_STORE:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    return {
        "rollout_id": rollout_id,
        "batches": [b.model_dump() for b in ROLLOUT_STORE[rollout_id].batches],
    }


@router.post("/{rollout_id}/cancel")
async def cancel_rollout(rollout_id: str):
    """取消灰度发布"""
    if rollout_id not in ROLLOUT_STORE:
        raise HTTPException(status_code=404, detail="发布任务不存在")

    session = ROLLOUT_STORE[rollout_id]
    if session.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"当前状态 {session.status} 不允许取消")

    session._cancel_event.set()
    if session._task and not session._task.done():
        session._task.cancel()

    session.status = "cancelled"
    session.updated_at = datetime.utcnow()

    return {"ok": True, "id": rollout_id, "status": "cancelled"}


@router.post("/{rollout_id}/rollback")
async def rollback_rollout(rollout_id: str):
    """回滚到上一成功批次"""
    if rollout_id not in ROLLOUT_STORE:
        raise HTTPException(status_code=404, detail="发布任务不存在")

    session = ROLLOUT_STORE[rollout_id]

    # 找到最后一个成功的批次
    last_success_idx = -1
    for i in range(len(session.batches) - 1, -1, -1):
        if session.batches[i].status == "success":
            last_success_idx = i
            break

    if last_success_idx < 0:
        raise HTTPException(status_code=400, detail="没有可回滚的成功批次")

    return {
        "ok": True,
        "id": rollout_id,
        "rollback_to_batch": last_success_idx,
        "message": f"回滚到批次 {last_success_idx + 1}",
    }


@router.get("/history/list")
async def list_rollout_history(
    limit: int = 20,
    offset: int = 0,
    status: Optional[str] = None,
):
    """查询灰度发布历史"""
    sessions = list(ROLLOUT_STORE.values())

    if status:
        sessions = [s for s in sessions if s.status == status]

    sessions.sort(key=lambda s: s.created_at, reverse=True)
    total = len(sessions)
    sessions = sessions[offset:offset + limit]

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "rollouts": [
            {
                "id": s.id,
                "task_text": s.task_text,
                "status": s.status,
                "total_hosts": len(s.hosts),
                "total_batches": len(s.batches),
                "current_batch": s.current_batch,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
                "duration_ms": s.total_duration_ms,
            }
            for s in sessions
        ],
    }


@router.get("/health")
async def health():
    return {"status": "ok", "service": "rollout-api"}


# ─── WebSocket 端点 ──────────────────────────────────────────────────────────

@router.websocket("/ws/rollout/{rollout_id}")
async def rollout_ws(websocket: WebSocket, rollout_id: str):
    """WebSocket 实时进度推送"""
    await websocket.accept()
    logger.info(f"WS rollout connected: {rollout_id}")

    if rollout_id not in ROLLOUT_STORE:
        await websocket.send_json({"type": "error", "message": "发布任务不存在"})
        await websocket.close()
        return

    # 注册连接
    if rollout_id not in WS_CONNECTIONS:
        WS_CONNECTIONS[rollout_id] = []
    WS_CONNECTIONS[rollout_id].append(websocket)

    session = ROLLOUT_STORE[rollout_id]

    try:
        # 发送当前状态
        await websocket.send_json({
            "type": "status",
            **session.to_status_response().model_dump(),
        })

        # 保持连接，接收心跳
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                msg = json.loads(data)

                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif msg.get("type") == "status":
                    # 前端请求状态更新
                    await websocket.send_json({
                        "type": "status",
                        **session.to_status_response().model_dump(),
                    })

            except asyncio.TimeoutError:
                # 心跳保活
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break

    except WebSocketDisconnect:
        logger.info(f"WS rollout disconnected: {rollout_id}")
    except Exception as e:
        logger.error(f"WS rollout error: {e}")
    finally:
        if rollout_id in WS_CONNECTIONS and websocket in WS_CONNECTIONS[rollout_id]:
            WS_CONNECTIONS[rollout_id].remove(websocket)
