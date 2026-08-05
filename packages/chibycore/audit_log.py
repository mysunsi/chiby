"""只追加 JSONL 审计日志（工业级 P0）。"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _default_audit_path() -> Path:
    root = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()
    p = os.environ.get("OPS_AUDIT_FILE", "").strip()
    if p:
        return Path(p)
    return root / "data" / "audit" / "ops_audit.jsonl"


def _max_bytes() -> int:
    try:
        mb = int(os.environ.get("OPS_AUDIT_MAX_MB", "80"))
    except ValueError:
        mb = 80
    return max(1, mb) * 1024 * 1024


class JsonlAuditLog:
    """线程安全的单行 JSON 追加；文件过大时简单轮转。"""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _default_audit_path()
        self._lock = threading.Lock()

    def append(self, record: Dict[str, Any]) -> None:
        rec = dict(record)
        rec.setdefault("ts", datetime.now(timezone.utc).isoformat())
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                if self._path.exists() and self._path.stat().st_size > _max_bytes():
                    rotated = self._path.with_suffix(self._path.suffix + ".1")
                    try:
                        if rotated.exists():
                            rotated.unlink()
                        self._path.rename(rotated)
                    except OSError as e:
                        logger.warning("审计轮转失败: %s", e)
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                logger.error("审计写入失败: %s", e)


_audit_singleton: Optional[JsonlAuditLog] = None
_singleton_lock = threading.Lock()


def get_audit_log() -> JsonlAuditLog:
    global _audit_singleton
    with _singleton_lock:
        if _audit_singleton is None:
            _audit_singleton = JsonlAuditLog()
        return _audit_singleton


def reset_audit_log_for_tests(path: Optional[Path] = None) -> None:
    global _audit_singleton
    with _singleton_lock:
        _audit_singleton = JsonlAuditLog(path) if path is not None else None


def append_audit(record: Dict[str, Any]) -> None:
    get_audit_log().append(record)
