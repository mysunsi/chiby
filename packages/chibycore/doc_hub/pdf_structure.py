"""PDF 字号聚类标题启发式：用 pypdf visitor 抽字号，构建简单层级。

无 pymupdf 依赖；抽不出结构时回退扁平并标记 structure_quality=low。
"""
from __future__ import annotations

import logging
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from chibycore.doc_hub.chunker_v2 import ParsedDocument, Section

logger = logging.getLogger(__name__)

# 相对正文主体字号：≥ 此倍率视为标题候选
_TITLE_SIZE_RATIO = 1.12
# 标题行最大字符数（过长更像正文）
_TITLE_MAX_CHARS = 80
# 至少多少字符的 span 才参与字号统计
_MIN_SPAN_CHARS = 2


@dataclass
class _Span:
    text: str
    size: float
    page: int


def _collect_spans(path: Path) -> List[_Span]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    spans: List[_Span] = []
    for pi, page in enumerate(reader.pages):
        buf: List[Tuple[str, float]] = []

        def visitor(text: str, _cm, _tm, _font_dict, font_size) -> None:
            t = (text or "").strip()
            if not t:
                return
            try:
                sz = float(font_size or 0)
            except (TypeError, ValueError):
                sz = 0.0
            if sz <= 0:
                sz = 10.0
            buf.append((t, sz))

        try:
            page.extract_text(visitor_text=visitor)
        except Exception as e:  # noqa: BLE001
            logger.debug("pdf visitor failed page %s: %s", pi, e)
            try:
                plain = page.extract_text() or ""
                if plain.strip():
                    spans.append(_Span(text=plain.strip(), size=10.0, page=pi))
            except Exception:  # noqa: BLE001
                pass
            continue

        # 合并同行同字号碎片
        line = ""
        line_sz = 0.0
        for t, sz in buf:
            if line and abs(sz - line_sz) > 0.4:
                if line.strip():
                    spans.append(_Span(text=line.strip(), size=line_sz, page=pi))
                line, line_sz = t, sz
            else:
                if not line:
                    line_sz = sz
                line += t
        if line.strip():
            spans.append(_Span(text=line.strip(), size=line_sz, page=pi))
    return spans


def _body_font_size(spans: List[_Span]) -> float:
    """按字符权重取众数/中位，作为正文主体字号。"""
    weighted: List[float] = []
    for s in spans:
        if len(s.text) < _MIN_SPAN_CHARS:
            continue
        # 截断极端大标题对众数的干扰：只统计中等长度段的字号票
        weight = min(len(s.text), 40)
        # 量化到 0.5pt
        rounded = round(s.size * 2) / 2.0
        weighted.extend([rounded] * max(1, weight // 4))
    if not weighted:
        sizes = [s.size for s in spans if s.size > 0]
        return float(statistics.median(sizes)) if sizes else 10.0
    counts = Counter(weighted)
    # 取出现最多的字号；并列时取较小（正文通常小于标题）
    mode_sz, _ = sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0]
    return float(mode_sz)


def _heading_level_for_size(size: float, body: float, title_sizes: List[float]) -> int:
    """把大于正文的字号映射到 1..3 级标题。"""
    uniq = sorted({round(s * 2) / 2.0 for s in title_sizes}, reverse=True)
    if not uniq:
        return 1
    rounded = round(size * 2) / 2.0
    try:
        idx = uniq.index(rounded)
    except ValueError:
        # 最近档
        idx = min(range(len(uniq)), key=lambda i: abs(uniq[i] - rounded))
    return min(3, idx + 1)


def _is_title_span(span: _Span, body: float) -> bool:
    if span.size < body * _TITLE_SIZE_RATIO:
        return False
    t = span.text.strip()
    if not t or len(t) > _TITLE_MAX_CHARS:
        return False
    # 纯数字/页码
    if t.isdigit():
        return False
    # 过短且无汉字/字母（噪声）
    if len(t) < 2:
        return False
    return True


def build_sections_from_spans(
    spans: List[_Span],
    *,
    doc_title: str = "",
) -> Tuple[List[Section], str]:
    """返回 (sections, structure_quality)。"""
    if not spans:
        return (
            [Section(title=doc_title or "正文", level=1, content="")],
            "low",
        )

    body = _body_font_size(spans)
    title_sizes = [s.size for s in spans if _is_title_span(s, body)]
    root: List[Section] = []
    stack: List[Section] = []
    title_count = 0

    for span in spans:
        if _is_title_span(span, body):
            lvl = _heading_level_for_size(span.size, body, title_sizes)
            sec = Section(title=span.text.strip(), level=lvl, content="")
            while stack and stack[-1].level >= lvl:
                stack.pop()
            if stack:
                stack[-1].children.append(sec)
            else:
                root.append(sec)
            stack.append(sec)
            title_count += 1
        else:
            text = span.text.strip()
            if not text:
                continue
            if stack:
                stack[-1].content += text + "\n"
            else:
                root.append(
                    Section(title=doc_title or "正文", level=1, content=text + "\n")
                )

    if not root:
        flat = "\n".join(s.text for s in spans)
        return (
            [Section(title=doc_title or "正文", level=1, content=flat)],
            "low",
        )

    # 质量：有多级标题且正文挂在标题下 → high；仅少量标题 → medium；几乎扁平 → low
    depth = 0

    def walk(secs: List[Section], d: int) -> None:
        nonlocal depth
        depth = max(depth, d)
        for s in secs:
            walk(s.children, d + 1)

    walk(root, 1)
    has_body_under = any(
        (s.content or "").strip() or s.children for s in root
    )
    if title_count >= 3 and depth >= 2 and has_body_under:
        quality = "high"
    elif title_count >= 1 and has_body_under:
        quality = "medium"
    else:
        quality = "low"

    return root, quality


def parse_pdf_sections(path: Path, *, doc_title: str = "") -> ParsedDocument:
    """解析 PDF 为 Section 树；失败则扁平 low。"""
    p = Path(path)
    title = doc_title or p.stem
    try:
        spans = _collect_spans(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("pdf structure extract failed: %s", e)
        from chibycore.doc_hub.parse import parse_file

        plain, t = parse_file(p)
        return ParsedDocument(
            title=t or title,
            sections=[Section(title=t or title or "正文", level=1, content=plain)],
            structure_quality="low",
        )

    sections, quality = build_sections_from_spans(spans, doc_title=title)
    # 若质量 low 且只有一个大节，仍可用；调用方会按句子切
    return ParsedDocument(title=title, sections=sections, structure_quality=quality)


def estimate_structure_quality(doc: ParsedDocument) -> str:
    if doc.structure_quality:
        return doc.structure_quality
    secs = doc.sections or []
    if len(secs) <= 1 and not (secs and secs[0].children):
        return "low"
    if any(s.children for s in secs):
        return "high"
    return "medium" if len(secs) >= 2 else "low"
