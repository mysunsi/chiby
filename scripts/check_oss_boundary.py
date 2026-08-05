#!/usr/bin/env python3
"""开源边界门禁（P0-8）：防止 packages/ 引用 proprietary，并可选检查 wheel。

用法::

    python scripts/check_oss_boundary.py
    python scripts/check_oss_boundary.py --wheel dist/*.whl
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"

_FORBIDDEN = re.compile(
    r"(?:^|\s)(?:from|import)\s+proprietary\b|"
    r"(?:^|\s)from\s+proprietary\.|"
    r"(?:^|\s)import\s+proprietary\.|"
    r"(?:^|\s)(?:from|import)\s+chiby_mobile\b|"
    r"(?:^|\s)from\s+chiby_mobile\.|"
    r"(?:^|\s)(?:from|import)\s+chiby_hermes_bridge\b|"
    r"(?:^|\s)from\s+chiby_hermes_bridge\.",
    re.M,
)

_WHEEL_BAD = (
    "proprietary/",
    "/mobile/",
    "mobile/",
    "hermes_bridge/",
    "/hermes_bridge/",
)


def scan_packages_imports() -> list[str]:
    hits: list[str] = []
    if not PACKAGES.is_dir():
        return [f"missing packages/ at {PACKAGES}"]
    for path in PACKAGES.rglob("*.py"):
        # 允许注释中提及 proprietary 品牌名？门禁只拦 import
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            hits.append(f"{path}: read failed: {exc}")
            continue
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if _FORBIDDEN.search(line):
                rel = path.relative_to(ROOT)
                hits.append(f"{rel}:{i}: {line.strip()}")
    return hits


def check_wheel(wheel: Path) -> list[str]:
    hits: list[str] = []
    if not wheel.is_file():
        return [f"wheel not found: {wheel}"]
    with zipfile.ZipFile(wheel, "r") as zf:
        names = zf.namelist()
    for n in names:
        low = n.replace("\\", "/")
        # 允许路径中偶然子串？严格：包路径段
        parts = low.split("/")
        if "proprietary" in parts:
            hits.append(f"wheel contains proprietary path: {n}")
        if "mobile" in parts and not n.endswith(".dist-info/METADATA"):
            # chibyterm/mobile 不应进 OSS wheel
            hits.append(f"wheel contains mobile path: {n}")
        if "hermes_bridge" in parts:
            hits.append(f"wheel contains hermes_bridge path: {n}")
    return hits


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OSS boundary gate")
    ap.add_argument(
        "--wheel",
        type=Path,
        action="append",
        default=[],
        help="optional .whl to inspect (repeatable)",
    )
    args = ap.parse_args(argv)

    errors = scan_packages_imports()
    for w in args.wheel:
        errors.extend(check_wheel(w))

    if errors:
        print("OSS boundary check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print("OSS boundary check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
