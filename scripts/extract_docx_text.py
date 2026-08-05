"""从工业级方案 docx 抽取纯文本（stdout）。"""
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def main() -> int:
    p = Path(__file__).resolve().parent.parent / "docs" / "工业级AI运维助手设计方案.docx"
    if len(sys.argv) > 1:
        p = Path(sys.argv[1])
    if not p.exists():
        print("missing:", p, file=sys.stderr)
        return 1
    with zipfile.ZipFile(p, "r") as z:
        xml = z.read("word/document.xml").decode("utf-8")
    root = ET.fromstring(xml)
    parts: list[str] = []
    for t in root.iter(f"{{{W_NS}}}t"):
        if t.text:
            parts.append(t.text)
    text = "".join(parts)
    text = re.sub(r" +", " ", text)
    out = Path(__file__).resolve().parent.parent / "docs" / "_docx_extracted.txt"
    if len(sys.argv) > 2:
        out = Path(sys.argv[2])
    out.write_text(text, encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
