"""语义感知切片：以标题结构为骨架，句子为原子。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple

_SENT_SPLIT = re.compile(r"(?<=[。！？；;\.\?\!\n])\s*")


@dataclass
class Section:
    title: str
    level: int
    content: str = ""
    children: List["Section"] = field(default_factory=list)


@dataclass
class ParsedDocument:
    title: str
    sections: List[Section] = field(default_factory=list)
    # high | medium | low —— PDF 扁平或抽不出标题时为 low
    structure_quality: str = ""


@dataclass
class SemanticChunk:
    ordinal: int
    text: str
    title_chain: str = ""


def _split_sentences(text: str) -> List[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    parts = [p.strip() for p in _SENT_SPLIT.split(raw) if p and p.strip()]
    return parts or [raw]


def _pack_sentences(
    sentences: Sequence[str],
    *,
    prefix: str,
    target_min: int,
    target_max: int,
) -> List[str]:
    """把句子装进 target_min~target_max 的块；块内以句子为单位重叠一句。"""
    if not sentences:
        return []
    out: List[str] = []
    buf: List[str] = []
    buf_len = 0
    pre_len = len(prefix) + (2 if prefix else 0)

    def flush(keep_last: bool = False) -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        body = "".join(buf).strip()
        if not body:
            buf, buf_len = [], 0
            return
        piece = f"{prefix}\n\n{body}" if prefix else body
        out.append(piece)
        if keep_last and buf:
            last = buf[-1]
            buf = [last]
            buf_len = len(last)
        else:
            buf, buf_len = [], 0

    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        # 单句超长：硬切
        if pre_len + len(s) > target_max and not buf:
            step = max(200, target_max - pre_len)
            for i in range(0, len(s), step):
                chunk_body = s[i : i + step]
                piece = f"{prefix}\n\n{chunk_body}" if prefix else chunk_body
                out.append(piece)
            continue
        if buf and pre_len + buf_len + len(s) > target_max:
            flush(keep_last=True)
        buf.append(s)
        buf_len += len(s)
        if pre_len + buf_len >= target_min and pre_len + buf_len >= int(target_max * 0.85):
            flush(keep_last=True)
    flush(keep_last=False)
    return out


def _walk(
    section: Section,
    chain: List[str],
    *,
    target_min: int,
    target_max: int,
    acc: List[Tuple[str, str]],
) -> None:
    title = (section.title or "").strip()
    next_chain = chain + ([title] if title else [])
    prefix = " > ".join(next_chain)
    body = (section.content or "").strip()
    if body:
        sents = _split_sentences(body)
        packed = _pack_sentences(
            sents, prefix=prefix, target_min=target_min, target_max=target_max
        )
        if not packed:
            piece = f"{prefix}\n\n{body}" if prefix else body
            if len(piece) <= target_max:
                acc.append((prefix, piece))
            else:
                for p in _pack_sentences(
                    [body], prefix=prefix, target_min=target_min, target_max=target_max
                ):
                    acc.append((prefix, p))
        else:
            for p in packed:
                acc.append((prefix, p))
    for child in section.children or []:
        _walk(
            child,
            next_chain,
            target_min=target_min,
            target_max=target_max,
            acc=acc,
        )


def chunk_parsed_document(
    doc: ParsedDocument,
    *,
    target_min: int = 280,
    target_max: int = 800,
) -> List[SemanticChunk]:
    """对结构化文档做语义切片。"""
    acc: List[Tuple[str, str]] = []
    sections = doc.sections or []
    if not sections:
        # 兜底：整篇
        flat = Section(title=doc.title or "", level=0, content="")
        _walk(flat, [], target_min=target_min, target_max=target_max, acc=acc)
    else:
        for sec in sections:
            _walk(
                sec,
                [],
                target_min=max(80, int(target_min)),
                target_max=max(200, int(target_max)),
                acc=acc,
            )
    out: List[SemanticChunk] = []
    for i, (chain, text) in enumerate(acc):
        t = (text or "").strip()
        if not t:
            continue
        out.append(SemanticChunk(ordinal=i, text=t, title_chain=chain))
    return out


def chunk_plain_text(
    text: str,
    *,
    title: str = "",
    target_min: int = 280,
    target_max: int = 800,
) -> List[SemanticChunk]:
    """无结构纯文本：按空行分段后语义装箱。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    root = Section(title=title or "", level=0, content="\n\n".join(paras) if paras else raw)
    return chunk_parsed_document(
        ParsedDocument(title=title or "", sections=[root]),
        target_min=target_min,
        target_max=target_max,
    )
