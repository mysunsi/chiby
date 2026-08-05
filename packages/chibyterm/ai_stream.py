"""WebSocket：AI 文本流（chunk）+ 最后一帧携带完整 llm_resp / 元数据。"""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

from chibycore.ai_stream_audit import append_ai_stream_event

_TT_DURATION_MS = {"thought": 1050, "action": 2000, "decision": 2600, "result": 650}


def _paragraphs_and_thoughts(text: str) -> List[Tuple[str, str, int, Optional[str]]]:
    """按空行切段并标注 thought_type / duration_ms / context。"""
    raw = (text or "").strip()
    if not raw:
        return [("", "result", _TT_DURATION_MS["result"], None)]
    parts = [p.strip() for p in re.split(r"\n\s*\n+", raw) if p.strip()]
    if not parts:
        parts = [raw]
    n = len(parts)
    out: List[Tuple[str, str, int, Optional[str]]] = []
    for i, para in enumerate(parts):
        if n <= 1:
            tt, dms = "result", _TT_DURATION_MS["result"]
        elif i == 0:
            tt, dms = "thought", _TT_DURATION_MS["thought"]
        elif i == n - 1:
            tt, dms = "result", _TT_DURATION_MS["result"]
        else:
            risky = ("安全", "网关", "风险", "拒绝", "依赖", "兼容", "权限", "sudo", "credential")
            if any(k in para for k in risky):
                tt, dms = "decision", _TT_DURATION_MS["decision"]
            else:
                tt, dms = "action", _TT_DURATION_MS["action"]
        ctx: Optional[str] = None
        if i > 0:
            prev = parts[i - 1].replace("\n", " ").strip()
            if len(prev) > 60:
                ctx = "上文要点：" + prev[-260:]
        out.append((para, tt, dms, ctx))
    return out


def _node_id_for_card(ai_card_id: str) -> str:
    aid = (ai_card_id or "").strip()
    return f"node_llm_{aid}" if aid else f"node_llm_{uuid.uuid4().hex[:12]}"


async def stream_llm_text_chunks(
    websocket: WebSocket,
    session_id: str,
    *,
    explanation: str,
    llm_resp: Dict[str, Any],
    chunk_chars: int = 40,
    stream_kind: str = "llm_resp",
) -> None:
    """
    发送 ai_stream_start → N × ai_stream_delta → ai_stream_end（内含 llm_resp）。
    浏览器侧负责渐进渲染；结束帧与原先单次 llm_resp 字段兼容。
    """
    message_id = str(uuid.uuid4())
    stream_id = str(uuid.uuid4())
    ai_card_id = str(llm_resp.get("ai_card_id") or "")
    node_id = _node_id_for_card(ai_card_id)

    seq = 0

    def audit(payload: Dict[str, Any]) -> None:
        append_ai_stream_event(session_id, payload)

    start_body: Dict[str, Any] = {
        "type": "ai_stream_start",
        "session_id": session_id,
        "message_id": message_id,
        "stream_id": stream_id,
        "node_id": node_id,
        "seq": seq,
        "phase": "llm",
        "stream_kind": stream_kind,
    }
    audit(start_body)
    await websocket.send_json(start_body)

    text_blocks = _paragraphs_and_thoughts(explanation or "")
    think_chunk_global = 0
    for para, thought_type, duration_ms, context in text_blocks:
        think_chunk_global += 1
        if not para:
            continue
        for i in range(0, len(para), chunk_chars):
            seq += 1
            delta = para[i : i + chunk_chars]
            chunk_body: Dict[str, Any] = {
                "type": "ai_stream_delta",
                "session_id": session_id,
                "message_id": message_id,
                "stream_id": stream_id,
                "node_id": node_id,
                "seq": seq,
                "delta": delta,
                "think_chunk": think_chunk_global,
                "thought_type": thought_type,
                "duration_ms": duration_ms,
            }
            if context:
                chunk_body["context"] = context
            audit(chunk_body)
            await websocket.send_json(chunk_body)

    seq += 1
    end_body: Dict[str, Any] = {
        "type": "ai_stream_end",
        "session_id": session_id,
        "message_id": message_id,
        "stream_id": stream_id,
        "node_id": node_id,
        "seq": seq,
        "stream_kind": stream_kind,
        "llm_resp": llm_resp,
    }
    audit(end_body)
    await websocket.send_json(end_body)


async def stream_plan_preview_text(
    websocket: WebSocket,
    session_id: str,
    *,
    explanation: str,
    plan_id: str,
    chunk_chars: int = 48,
) -> None:
    """计划预览前仅流式推送说明文字；不包含 llm_resp（后续仍发 llm_plan）。"""
    message_id = str(uuid.uuid4())
    stream_id = str(uuid.uuid4())
    node_id = f"plan_preview_{plan_id}"

    seq = 0

    def audit(payload: Dict[str, Any]) -> None:
        append_ai_stream_event(session_id, payload)

    start_body = {
        "type": "ai_stream_start",
        "session_id": session_id,
        "message_id": message_id,
        "stream_id": stream_id,
        "node_id": node_id,
        "seq": seq,
        "phase": "plan",
        "stream_kind": "plan_explanation",
        "plan_id": plan_id,
    }
    audit(start_body)
    await websocket.send_json(start_body)

    text_blocks = _paragraphs_and_thoughts(explanation or "")
    think_chunk_global = 0
    for para, thought_type, duration_ms, context in text_blocks:
        think_chunk_global += 1
        if not para:
            continue
        for i in range(0, len(para), chunk_chars):
            seq += 1
            delta = para[i : i + chunk_chars]
            chunk_body: Dict[str, Any] = {
                "type": "ai_stream_delta",
                "session_id": session_id,
                "message_id": message_id,
                "stream_id": stream_id,
                "node_id": node_id,
                "seq": seq,
                "delta": delta,
                "plan_id": plan_id,
                "think_chunk": think_chunk_global,
                "thought_type": thought_type,
                "duration_ms": duration_ms,
            }
            if context:
                chunk_body["context"] = context
            audit(chunk_body)
            await websocket.send_json(chunk_body)

    seq += 1
    end_body = {
        "type": "ai_stream_end",
        "session_id": session_id,
        "message_id": message_id,
        "stream_id": stream_id,
        "node_id": node_id,
        "seq": seq,
        "stream_kind": "plan_explanation",
        "plan_id": plan_id,
        "llm_resp": None,
    }
    audit(end_body)
    await websocket.send_json(end_body)
