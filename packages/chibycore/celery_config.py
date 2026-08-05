"""Celery 应用配置 + 任务定义。

本地开发模式（ALWAYS_EAGER=True）：任务同步执行，无需 Redis/Worker。
生产模式：设置 CELERY_BROKER_URL + CELERY_RESULT_BACKEND 即可切换。
"""
from __future__ import annotations

import logging
import os

from celery import Celery

logger = logging.getLogger(__name__)

# ── Broker 配置 ────────────────────────────────────────────────────────────────
_is_local = os.getenv("OPS_CELERY_LOCAL", "true").lower() == "true"

if _is_local:
    # 本地模式：同步执行，不需要 broker
    celery_app = Celery("ops_assistant")
    celery_app.conf.update(
        broker_url="rpc://",
        result_backend="rpc://",
        task_always_eager=True,
        task_eager_propagate=True,
        result_expires=300,
        broker_connection_retry_on_startup=True,
    )
    logger.info("Celery 本地模式（ALWAYS_EAGER）")
else:
    # 生产模式
    broker = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    celery_app = Celery("ops_assistant")
    celery_app.conf.update(
        broker_url=broker,
        result_backend=backend,
        task_always_eager=False,
        result_expires=3600,
        task_track_started=True,
        task_time_limit=600,
        worker_prefetch_multiplier=1,
    )
    logger.info(f"Celery 生产模式，broker={broker}")

celery_app.autodiscover_tasks(["chibycore"])

__all__ = ["celery_app"]
