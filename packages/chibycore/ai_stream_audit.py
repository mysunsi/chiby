"""AI 流式输出审计：仅追加 JSONL，供回放与合规；与终端 WS 协议字段一致。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# OPS_AI_STREAM_AUDIT=0 可关闭；默认与 OPS_TRANSCRIPT 行为类似
_ROOT: Optional[Path] = None


def _root() -> Path:
    global _ROOT
    if _ROOT is None:
        _ROOT = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "transcripts" / "ai_stream"
    return _ROOT


def ai_stream_audit_enabled() -> bool:
    if os.environ.get("OPS_AI_STREAM_AUDIT", "1").strip() in ("0", "false", "no"):
        return False
    return True


def append_ai_stream_event(session_id: str, event: Dict[str, Any]) -> None:
    """落盘单条事件（已含 type / seq 等）。"""
    if not ai_stream_audit_enabled() or not session_id:
        return
    try:
        _root().mkdir(parents=True, exist_ok=True)
        path = _root() / f"{session_id}.jsonl"
        rec: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
        }
        # 不重复 session_id
        for k, v in event.items():
            if k not in rec:
                rec[k] = v
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_ai_stream_events(
    session_id: str, *, max_events: int = 20000
) -> List[Dict[str, Any]]:
    """按文件顺序读取（适合回放重放）。"""
    path = _root() / f"{session_id}.jsonl"
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    if max_events and len(out) > max_events:
        return out[-max_events:]
    return out
