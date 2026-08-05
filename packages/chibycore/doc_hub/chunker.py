"""文本切片（字符近似 token，重叠约 15%）。"""
from __future__ import annotations

from typing import List, Tuple


def chunk_text(
    text: str,
    *,
    chunk_size: int = 1000,
    overlap_ratio: float = 0.15,
) -> List[Tuple[int, str]]:
    """返回 [(ordinal, chunk_text), ...]。空文本返回空列表。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    size = max(200, int(chunk_size))
    overlap = max(0, min(size - 1, int(size * float(overlap_ratio))))
    step = max(1, size - overlap)

    chunks: List[Tuple[int, str]] = []
    i = 0
    ordinal = 0
    n = len(raw)
    while i < n:
        piece = raw[i : i + size].strip()
        if piece:
            chunks.append((ordinal, piece))
            ordinal += 1
        if i + size >= n:
            break
        i += step
    return chunks
