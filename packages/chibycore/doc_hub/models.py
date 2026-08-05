"""DocHub 数据模型。"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DocStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class DocumentRecord(BaseModel):
    id: str
    title: str
    source_path: str = ""
    stored_path: str = ""
    mime_or_ext: str = ""
    status: DocStatus = DocStatus.PENDING
    chunk_count: int = 0
    error: str = ""
    bytes_size: int = 0
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())

    def model_dump_compat(self) -> Dict[str, Any]:
        d = self.model_dump()
        d["status"] = self.status.value if isinstance(self.status, DocStatus) else str(self.status)
        return d


class ChunkRecord(BaseModel):
    id: str
    doc_id: str
    ordinal: int
    text: str
    title: str = ""
    source_path: str = ""
    title_chain: str = ""


class SearchHit(BaseModel):
    doc_id: str
    chunk_id: str
    title: str = ""
    source_path: str = ""
    snippet: str = ""
    score: float = 0.0
    ordinal: int = 0
    title_chain: str = ""


class SearchResponse(BaseModel):
    query: str
    total: int
    results: List[SearchHit] = Field(default_factory=list)
    took_ms: int = 0
    strategy: str = "vector"
    debug: Optional[Dict[str, Any]] = None
