"""文档解析：md/txt 必做；pdf/docx 可选依赖。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".md", ".txt", ".markdown", ".pdf", ".docx"}


def sniff_title(path: Path, text: str) -> str:
    name = path.stem.strip() or path.name
    for line in (text or "").splitlines()[:30]:
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or name
        if s:
            return s[:120]
    return name


def parse_file(path: Path) -> Tuple[str, str]:
    """解析文件 → (plain_text, title)。不支持则抛 ValueError。"""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"文件不存在: {p}")
    suf = p.suffix.lower()
    if suf not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的格式: {suf}（支持 {sorted(SUPPORTED_SUFFIXES)}）")

    if suf in (".md", ".txt", ".markdown"):
        text = p.read_text(encoding="utf-8", errors="replace")
        return text, sniff_title(p, text)

    if suf == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise ValueError("缺少 pypdf，无法解析 PDF：pip install pypdf") from e
        reader = PdfReader(str(p))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception as ex:  # noqa: BLE001
                logger.debug("PDF page extract failed: %s", ex)
        text = "\n".join(parts)
        return text, sniff_title(p, text)

    if suf == ".docx":
        try:
            from docx import Document as DocxDocument
        except ImportError as e:
            raise ValueError("缺少 python-docx，无法解析 DOCX：pip install python-docx") from e
        doc = DocxDocument(str(p))
        text = "\n".join(para.text for para in doc.paragraphs)
        return text, sniff_title(p, text)

    raise ValueError(f"不支持的格式: {suf}")
