#!/usr/bin/env python3
"""提交前检查：工作副本里尚未 ``svn add`` 的源码/静态资源。

背景：SVN 不会自动纳管新建文件；对端 ``svn update`` 拿不到 ``?`` 状态文件，
易出现 ``ModuleNotFoundError`` / vendor 404 / ACP Internal error。

用法（仓库 ``D:\\Open`` 或 ``Assistant`` 目录下均可）::

  python Assistant/scripts/check_svn_unversioned.py
  python scripts/check_svn_unversioned.py --root D:\\Open
  python scripts/check_svn_unversioned.py --strict

``--strict``：存在可疑未纳管源码时退出码 1（适合挂 CI / 提交前钩子）。
默认只打印报告，退出码 0（便于日常扫一眼）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

# 相对 SVN 根（通常为 D:\\Open）要扫描的子树
DEFAULT_SCAN = ("Assistant", "Hermes")

# 明确视为「应进库」的扩展名
SOURCE_SUFFIXES = {
    ".py",
    ".pyi",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".toml",
    ".svg",
    ".md",
    ".json",
}

# 路径片段：命中则忽略（目录名或文件名）
IGNORE_PARTS = {
    ".svn",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    ".egg-info",
    "hermes_agent.egg-info",
    ".plans",
    ".github",
    "coverage",
    "htmlcov",
}

# 文件名精确忽略
IGNORE_NAMES = {
    ".env",
    ".env.local",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Thumbs.db",
    ".DS_Store",
}

# 相对路径 glob 风格忽略（简单 endswith / 子串）
IGNORE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".tmp",
    ".bak",
    ".swp",
}


def _find_svn_root(start: Path) -> Optional[Path]:
    cur = start.resolve()
    for p in [cur, *cur.parents]:
        if (p / ".svn").is_dir():
            return p
    return None


def _ignored(rel_posix: str) -> bool:
    name = Path(rel_posix).name
    if name in IGNORE_NAMES:
        return True
    if name.endswith(".egg-info"):
        return True
    lower = rel_posix.replace("\\", "/").lower()
    parts = set(lower.split("/"))
    if parts & {p.lower() for p in IGNORE_PARTS}:
        return True
    for suf in IGNORE_SUFFIXES:
        if lower.endswith(suf):
            return True
    # 常见本地数据 / 密钥
    if "/data/" in f"/{lower}" and lower.endswith((".json", ".db", ".sqlite")):
        # data 下配置示例应进库；运行时库文件忽略
        if any(x in lower for x in ("knowledge_hub", "state.db", "audit", "chat_audit")):
            return True
    return False


def _is_source(rel_posix: str) -> bool:
    suf = Path(rel_posix).suffix.lower()
    return suf in SOURCE_SUFFIXES


def _run_svn_status(root: Path, paths: Sequence[str]) -> str:
    cmd = ["svn", "status", "--ignore-externals"]
    cmd.extend(paths)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        print("错误: 找不到 svn 命令，请先安装 Subversion 客户端并加入 PATH。", file=sys.stderr)
        sys.exit(2)
    if proc.returncode not in (0, 1):
        # status 对部分缺失路径可能非 0；仍尽量解析 stdout
        err = (proc.stderr or "").strip()
        if err and not (proc.stdout or "").strip():
            print(f"错误: svn status 失败 (code={proc.returncode}): {err}", file=sys.stderr)
            sys.exit(2)
    return proc.stdout or ""


_STATUS_RE = re.compile(r"^(?P<code>[ACDIMRX?!~ ])(?P<code2>.)\s+(?P<path>.+)$")


def _parse_unversioned(status_text: str) -> List[str]:
    out: List[str] = []
    for line in status_text.splitlines():
        line = line.rstrip()
        if not line or line.startswith("Performing") or line.startswith("---"):
            continue
        # svn status: 第 1 列 '?' = 未纳管
        if line[0] != "?":
            continue
        path = line[1:].strip()
        # 偶发两列状态后空格
        if path.startswith("     ") or path.startswith("\t"):
            path = path.strip()
        # 标准格式 "?       path"
        m = re.match(r"^\?\s+(.+)$", line)
        if m:
            path = m.group(1).strip()
        path = path.replace("\\", "/")
        out.append(path)
    return out


def _module_candidates(rel_posix: str) -> List[str]:
    """由相对路径推断可能的 import 名（粗略）。"""
    p = rel_posix.replace("\\", "/")
    if not p.endswith(".py"):
        return []
    if p.endswith("/__init__.py"):
        mod_path = p[: -len("/__init__.py")]
    else:
        mod_path = p[: -len(".py")]
    parts = mod_path.split("/")
    # 去掉顶层工程名（Assistant / Hermes）
    if parts and parts[0] in ("Assistant", "Hermes"):
        parts = parts[1:]
    if not parts:
        return []
    dotted = ".".join(parts)
    # terminal.mobile.chat_audit / chibycore.exec_ticket / acp_adapter.ops_plan
    cands = [dotted]
    if dotted.startswith("acp_adapter."):
        cands.append(dotted)
    return cands


def _scan_imports_referencing(
    root: Path,
    scan_dirs: Sequence[str],
    module_names: Sequence[str],
) -> List[Tuple[str, str]]:
    """在已存在的 .py 中搜索 import；返回 (引用文件, 模块名)。"""
    if not module_names:
        return []
    # 构造简单正则：from X import | import X
    patterns = []
    for mod in module_names:
        esc = re.escape(mod)
        patterns.append(re.compile(rf"(?:from\s+{esc}\s+import|import\s+{esc})\b"))
    hits: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for top in scan_dirs:
        base = root / top
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            rel = py.relative_to(root).as_posix()
            if _ignored(rel):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for mod, pat in zip(module_names, patterns):
                if pat.search(text):
                    key = (rel, mod)
                    if key not in seen:
                        seen.add(key)
                        hits.append(key)
    return hits


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="检查未 svn add 的源码文件")
    ap.add_argument(
        "--root",
        type=Path,
        default=None,
        help="SVN 工作副本根（默认从当前目录或脚本位置向上查找 .svn）",
    )
    ap.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=None,
        help="相对根目录的扫描路径，可重复（默认 Assistant + Hermes）",
    )
    ap.add_argument(
        "--strict",
        action="store_true",
        help="存在可疑未纳管源码时以退出码 1 结束",
    )
    ap.add_argument(
        "--all-unversioned",
        action="store_true",
        help="一并列出被忽略规则过滤掉的未纳管路径（调试用）",
    )
    args = ap.parse_args(list(argv) if argv is not None else None)

    start = Path.cwd()
    root = args.root
    if root is None:
        root = _find_svn_root(start) or _find_svn_root(Path(__file__).resolve().parent)
    else:
        root = root.resolve()
        if not (root / ".svn").is_dir():
            # 允许传入 Open 子目录
            found = _find_svn_root(root)
            if found:
                root = found
    if root is None or not (root / ".svn").is_dir():
        print("错误: 未找到 SVN 工作副本（.svn）。请在 D:\\Open 下运行或传 --root。", file=sys.stderr)
        return 2

    paths = list(args.paths) if args.paths else list(DEFAULT_SCAN)
    existing = [p for p in paths if (root / p).exists()]
    if not existing:
        print(f"错误: 扫描路径均不存在（root={root} paths={paths}）", file=sys.stderr)
        return 2

    print(f"SVN 根: {root}")
    print(f"扫描:   {', '.join(existing)}")
    status = _run_svn_status(root, existing)
    unversioned = _parse_unversioned(status)

    suspicious: List[str] = []
    skipped: List[str] = []
    for rel in sorted(set(unversioned)):
        if _ignored(rel):
            skipped.append(rel)
            continue
        if _is_source(rel):
            suspicious.append(rel)
        else:
            skipped.append(rel)

    if not suspicious:
        print("未发现可疑的未纳管源码文件（?）。")
        if args.all_unversioned and skipped:
            print(f"\n其它未纳管（已忽略规则）共 {len(skipped)} 项，示例：")
            for rel in skipped[:20]:
                print(f"  ? {rel}")
            if len(skipped) > 20:
                print(f"  … 另有 {len(skipped) - 20} 项")
        return 0

    print(f"\n发现 {len(suspicious)} 个未 svn add 的源码/资源文件：")
    for rel in suspicious:
        print(f"  ? {rel}")

    # 交叉：这些模块是否已被已有代码 import
    mods: List[str] = []
    for rel in suspicious:
        mods.extend(_module_candidates(rel))
    mods = sorted(set(mods))
    refs = _scan_imports_referencing(root, existing, mods)
    # 只保留「引用方自身已在版本库」的告警：引用文件不在 suspicious 里
    sus_set = set(suspicious)
    hot = [(f, m) for f, m in refs if f not in sus_set]
    if hot:
        print("\n高风险：已有代码 import 了未纳管模块（对端 update 后极易报错）：")
        for f, m in hot[:40]:
            print(f"  {f}  →  {m}")
        if len(hot) > 40:
            print(f"  … 另有 {len(hot) - 40} 处")

    print("\n建议：")
    print("  svn add <上述文件>")
    print("  svn commit -m \"…\"")
    print("或对目录： svn add Assistant/terminal/mobile/foo.py")

    if args.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
