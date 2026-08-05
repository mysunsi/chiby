"""Ops Assistant 配置。"""
from __future__ import annotations

import os

# 执行模式
EXECUTION_MODE = os.getenv("OPS_EXECUTION_MODE", "local")  # local | mock
LOCAL_HOST = "127.0.0.1"
DEFAULT_USER = os.getenv("USER", "sunsi")

# 执行超时（秒）
CMD_TIMEOUT = 30

# 失败重试次数
MAX_RETRIES = 2

# 日志级别
LOG_LEVEL = os.getenv("OPS_LOG_LEVEL", "INFO")
