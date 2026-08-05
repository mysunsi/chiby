"""会话全量 transcript：输出 + 可选整行输入，JSONL 只追加。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from chibycore.redaction import redact_command_text

# OPS_TRANSCRIPT=0 关闭；默认开启。根目录下 data/transcripts/
_TRANSCRIPT_ROOT: Optional[Path] = None


def _root() -> Path:
    global _TRANSCRIPT_ROOT
    if _TRANSCRIPT_ROOT is None:
        base = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "transcripts"
        _TRANSCRIPT_ROOT = base
    return _TRANSCRIPT_ROOT


def transcript_enabled() -> bool:
    return os.environ.get("OPS_TRANSCRIPT", "1").strip() not in ("0", "false", "no")


def append_transcript(session_id: str, direction: str, payload: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """direction: out | in"""
    if not transcript_enabled() or not session_id:
        return
    try:
        _root().mkdir(parents=True, exist_ok=True)
        path = _root() / f"{session_id}.jsonl"
        rec: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "direction": direction,
            "data": redact_command_text(payload, max_len=64000),
        }
        if extra:
            rec.update(extra)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
