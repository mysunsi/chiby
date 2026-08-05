"""终端捕获 / exec / LLM 上下文的输出预算常量（避免散落魔法数）。"""
from __future__ import annotations

import os
from typing import Tuple

# ── 左侧终端 output_capture 环形缓冲（session_manager）──────────────────────
TERMINAL_CAPTURE_RING_MAX_CHARS: int = int(
    os.environ.get("OPS_TERMINAL_CAPTURE_MAX_CHARS", "48000")
)

# ── 自然语言 WS：上下文拼接（terminal/main.py llm handler）──────────────────
NL_CONTEXT_TAIL_CHARS: int = int(os.environ.get("OPS_NL_CONTEXT_TAIL_CHARS", "6000"))
NL_CONTEXT_JOIN_CAP_CHARS: int = int(os.environ.get("OPS_NL_CONTEXT_JOIN_CAP_CHARS", "8000"))
NL_WS_CONTEXT_BUFFER_LINES: int = int(os.environ.get("OPS_NL_WS_CONTEXT_BUFFER_LINES", "20"))
NL_OUTPUT_BUFFER_MAX_LINES: int = int(os.environ.get("OPS_NL_OUTPUT_BUFFER_MAX_LINES", "50"))

# ── 闭环 / LLM 判定 / KB 摘录 ───────────────────────────────────────────────
CLOSURE_STDIO_TAIL_CHARS: int = int(os.environ.get("OPS_CLOSURE_STDIO_TAIL_CHARS", "6000"))
CLOSURE_ARCHIVE_TAIL_CHARS: int = int(os.environ.get("OPS_CLOSURE_ARCHIVE_TAIL_CHARS", "8000"))

# ── REST/网关快照中的 exec 输出尾部（intent 广播、closure step API）──────────
API_EXEC_IO_TAIL_CHARS: int = int(os.environ.get("OPS_API_EXEC_IO_TAIL_CHARS", "4000"))

# ── 闭环 SSE 单步全文上限（与 streaming chunk 上限对齐）──────────────────────
CLOSURE_FULL_STREAM_CAP_CHARS: int = int(
    os.environ.get("OPS_CLOSURE_FULL_STREAM_CAP_CHARS", str(512 * 1024))
)

# ── 计划步骤验证：终端捕获尾部（main merge_nl_payload）───────────────────────
PLAN_VERIFY_CAPTURE_FALLBACK_CHARS: int = int(
    os.environ.get("OPS_PLAN_VERIFY_CAPTURE_FALLBACK_CHARS", "4000")
)
PLAN_VERIFY_WS_TAIL_CHARS: int = int(os.environ.get("OPS_PLAN_VERIFY_WS_TAIL_CHARS", "4000"))
PLAN_VERIFY_MERGE_TAIL_CHARS: int = int(os.environ.get("OPS_PLAN_VERIFY_MERGE_TAIL_CHARS", "8000"))

# ── WebSocket 命令集输出预览（main）──────────────────────────────────────────
UI_WS_CHUNK_PREVIEW_CHARS: int = int(os.environ.get("OPS_UI_WS_CHUNK_PREVIEW_CHARS", "24000"))
UI_WS_SUMMARY_TAIL_CHARS: int = int(os.environ.get("OPS_UI_WS_SUMMARY_TAIL_CHARS", "16000"))

# ── KB 闭环候选摘录（knowledge_hub/closure_candidate）───────────────────────
KB_PENDING_FINAL_IO_TAIL_CHARS: int = int(os.environ.get("OPS_KB_PENDING_FINAL_IO_TAIL_CHARS", "4000"))

# ── 本地 oneshot 子进程（local_oneshot）──────────────────────────────────────
LOCAL_ONESHOT_STREAM_READ_CHUNK: int = 4096
LOCAL_ONESHOT_MAX_COMBINED_OUTPUT_CHARS: int = int(
    os.environ.get("OPS_LOCAL_ONESHOT_MAX_OUTPUT_CHARS", str(512 * 1024))
)

# ── 审计 / transcript ─────────────────────────────────────────────────────────
TRANSCRIPT_PAYLOAD_TAIL_CHARS: int = int(os.environ.get("OPS_TRANSCRIPT_TAIL_CHARS", str(64 * 1024)))

# ── llm_shell 对话截断（与 max_tokens 协同）───────────────────────────────────
LLM_CHAT_CONTEXT_CHAR_BUDGET_MULT: int = 6  # ctx_budget ≈ max_tokens * mult（粗略）


def truncate_text(text: str, max_chars: int, *, suffix: str = "\n… [truncated]") -> Tuple[str, bool]:
    """返回 (截断后文本, 是否发生过截断)。"""
    if max_chars <= 0 or not text:
        return text, False
    if len(text) <= max_chars:
        return text, False
    head = max_chars - len(suffix)
    if head <= 0:
        return suffix.strip(), True
    return text[:head] + suffix, True
