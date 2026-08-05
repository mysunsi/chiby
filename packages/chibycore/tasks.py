"""Celery 异步任务定义。"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def execute_ops_task(self, command: str, host: str, user: str, password: str) -> Dict[str, Any]:
    """异步执行运维任务（Celery 任务）。"""
    task_id = str(uuid.uuid4())[:8]

    # 导入放在内部避免顶层循环导入
    from chibycore.schemas import TaskRequest
    from chibycore.engine import run_task
    from chibycore import database as _db

    logger.info(f"[{task_id}] Celery 任务开始: {command}")

    try:
        req = TaskRequest(
            command=command,
            host=host,
            ssh_user=user,
            ssh_password=password,
        )
        result = run_task(req)

        # 更新数据库状态（task_id 已知）
        try:
            _db.get_session().close()
        except Exception:
            pass

        return {
            "task_id": result.task_id,
            "status": result.status,
            "chain_name": getattr(result, "parsed_params", {}).get("chain_name"),
            "steps_count": len(result.steps),
            "duration_ms": result.total_duration_ms,
            "output": (result.final_output or "")[:500],
            "error": result.error_message,
        }

    except Exception as exc:
        logger.error(f"[{task_id}] Celery 任务异常: {exc}")
        raise self.retry(exc=exc, countdown=10)


@shared_task(bind=True)
def check_task_status(self, celery_task_id: str) -> Dict[str, Any]:
    """查询 Celery 任务状态。"""
    try:
        async_result = self.AsyncResult(celery_task_id)
        return {
            "celery_task_id": celery_task_id,
            "status": async_result.status,
            "result": async_result.result if async_result.ready() else None,
            "info": str(async_result.info) if async_result.info else None,
        }
    except Exception as exc:
        return {"celery_task_id": celery_task_id, "status": "ERROR", "error": str(exc)}


@shared_task(bind=True)
def cleanup_old_history(self, days: int = 30) -> Dict[str, Any]:
    """清理 N 天前的历史记录。"""
    from chibycore import database as _db
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(days=days)
    session = _db.get_session()
    try:
        from chibycore.database import TaskHistory
        deleted = session.query(TaskHistory).filter(
            TaskHistory.created_at < cutoff
        ).delete()
        session.commit()
        logger.info(f"清理了 {deleted} 条历史记录（>{days}天）")
        return {"deleted": deleted, "cutoff": cutoff.isoformat()}
    except Exception as exc:
        session.rollback()
        return {"error": str(exc)}
    finally:
        session.close()
