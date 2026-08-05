#!/usr/bin/env python3
"""
export_oss_mirror.py - 从内部单体仓装配「仅开源(OSS)子集」的干净目录树。

本脚本只负责"装配"，绝不主动推送(push)。装配完成后由人工 / CI 做
secret 扫描与复核，再推送到公开镜像仓库。

用法:
  python scripts/export_oss_mirror.py --src D:/Open/Assistant --out D:/tmp/ops-bridge-oss
  python scripts/export_oss_mirror.py --src . --out /tmp/oss --dry-run

流程:
  1. 读取 release/oss_manifest.json(allow/deny 规则)。
  2. 把 allow 列表中的顶层路径拷贝到 <out>，凡命中 denylist 的路径丢弃。
  3. 把 release/templates/* 注入 <out> 根目录(LICENSE / NOTICE / README ...)。
  4. 把 release/ci/*.yml 注入 <out>/.github/workflows/。
  5. 可选: 构建 ops-ui、运行 secret 扫描(gitleaks/trufflehog)。
  6. 打印汇总。绝不推送，绝不打印/要求密钥。

安全约定:
  - 默认不推送；推送是独立、显式的步骤。
  - 所有敏感路径(terminal/mobile, terminal/hermes_bridge, data, .env ...)
    默认在 denylist 中，漏筛由 secret 扫描二次兜底。
  - 规则清单(oss_manifest.json)在 P0 解耦完成后应由开发+法务复核微调。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---- 基础卫生规则(始终生效，不进清单也好用) ----
ALWAYS_GLOB_PARTS = ["__pycache__", "node_modules", ".venv", ".pytest_cache"]


def iter_tree(root: Path):
    """生成 (abs_path, rel_path) 对所有文件。"""
    for dirpath, dirnames, filenames in os.walk(root):
        # 剪枝: 卫生目录直接跳过子树
        dirnames[:] = [d for d in dirnames if d not in ALWAYS_GLOB_PARTS]
        for fn in filenames:
            if fn in ALWAYS_GLOB_PARTS:
                continue
            abs_p = Path(dirpath) / fn
            rel = abs_p.relative_to(root).as_posix()
            yield abs_p, rel


def in_allow(rel: str, allow: list[str]) -> bool:
    for a in allow:
        if rel == a or rel.startswith(a + "/") or rel.startswith(a + "\\"):
            return True
    return False


def is_denied(rel: str, m: dict) -> bool:
    parts = rel.split("/")
    # 路径前缀拒绝(整棵子树)
    for p in m.get("deny_path_prefixes", []):
        if rel == p or rel.startswith(p + "/"):
            return True
    # 精确文件名拒绝
    fname = parts[-1]
    if fname in m.get("deny_filenames", []):
        return True
    # 仓库根目录的临时/草稿文件(下划线前缀)
    if len(parts) == 1 and fname.startswith("_"):
        return True
    # tests/ 下文件名命中专有测试
    if parts[0] == "tests" and any(t in fname for t in m.get("deny_tests_contains", [])):
        return True
    # docs/ 下文件名命中内部文档
    if parts[0] == "docs" and any(t in fname for t in m.get("deny_docs_contains", [])):
        return True
    return False


def copy_tree(src_root: Path, out_root: Path, m: dict, dry: bool):
    allowed, denied = [], []
    for abs_p, rel in iter_tree(src_root):
        if not in_allow(rel, m.get("allow", [])):
            continue
        if is_denied(rel, m):
            denied.append(rel)
            continue
        allowed.append(rel)
        if not dry:
            dest = out_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_p, dest)
    return allowed, denied


def inject_templates(src_root: Path, out_root: Path, dry: bool):
    tpl = src_root / "release" / "templates"
    copied = []
    if not tpl.exists():
        print(f"[warn] templates dir missing: {tpl}")
        return copied
    for f in tpl.iterdir():
        if f.is_file():
            copied.append(f.name)
            if not dry:
                shutil.copy2(f, out_root / f.name)
    # CI 工作流
    ci = src_root / "release" / "ci"
    if ci.exists():
        wf = out_root / ".github" / "workflows"
        if not dry:
            wf.mkdir(parents=True, exist_ok=True)
        for f in ci.glob("*.yml"):
            copied.append(f".github/workflows/{f.name}")
            if not dry:
                shutil.copy2(f, wf / f.name)
    return copied


def maybe_build_ui(src_root: Path, dry: bool):
    if dry:
        return "skip(dry-run)"
    ui = src_root / "ops-ui"
    if not (ui / "package.json").exists():
        return "no ops-ui"
    try:
        subprocess.run(["npm", "--prefix", str(ui), "ci"], check=True, capture_output=True)
        subprocess.run(["npm", "--prefix", str(ui), "run", "build"], check=True, capture_output=True)
        # 把产物放进 chibyterm/static(若导出树含该目录)
        return "built"
    except Exception as e:  # noqa
        return f"build skipped: {e}"


def maybe_secret_scan(out_root: Path, skip: bool, dry: bool):
    if skip or dry:
        return "skipped"
    for tool in ("gitleaks", "trufflehog"):
        if shutil.which(tool):
            try:
                if tool == "gitleaks":
                    r = subprocess.run([tool, "detect", "--source", str(out_root), "--no-banner"],
                                       capture_output=True, text=True)
                else:
                    r = subprocess.run([tool, "filesystem", str(out_root)],
                                       capture_output=True, text=True)
                return f"{tool}: {'clean' if r.returncode == 0 else 'HITS - review!'}"
            except Exception:
                continue
    return "no scanner installed (CI must enforce gitleaks)"


def main():
    ap = argparse.ArgumentParser(description="Assemble OSS-only mirror tree from monorepo.")
    ap.add_argument("--src", default=".", help="monorepo root (default: cwd)")
    ap.add_argument("--out", required=True, help="output clean tree directory")
    ap.add_argument("--manifest", default=None, help="path to oss_manifest.json")
    ap.add_argument("--dry-run", action="store_true", help="plan only, write nothing")
    ap.add_argument("--build-ui", action="store_true", help="npm build ops-ui into tree")
    ap.add_argument("--skip-secret-scan", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite existing --out")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    out = Path(args.out).resolve()
    if not src.exists():
        print(f"[error] src not found: {src}")
        return 2
    if out.resolve() == src.resolve() or src.resolve() in out.resolve().parents:
        print("[error] --out must not be inside --src (refusing).")
        return 2
    if out.exists():
        if not args.force and not args.dry_run:
            print(f"[error] --out exists: {out} (use --force to overwrite)")
            return 2
        if not args.dry_run:
            shutil.rmtree(out)
    if not args.dry_run:
        out.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(args.manifest) if args.manifest else src / "release" / "oss_manifest.json"
    if not manifest_path.exists():
        print(f"[error] manifest not found: {manifest_path}")
        return 2
    m = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"== export_oss_mirror ({'DRY-RUN' if args.dry_run else 'WRITE'}) ==")
    print(f"src : {src}")
    print(f"out : {out}")
    print(f"manifest: {manifest_path}")

    allowed, denied = copy_tree(src, out, m, args.dry_run)
    injected = inject_templates(src, out, args.dry_run)
    ui = maybe_build_ui(src, args.dry_run) if args.build_ui else "disabled"
    scan = maybe_secret_scan(out, args.skip_secret_scan, args.dry_run)

    # 汇总
    total_bytes = 0
    if not args.dry_run:
        for p in out.rglob("*"):
            if p.is_file():
                total_bytes += p.stat().st_size
    print(f"\n-- summary --")
    print(f"copied files : {len(allowed)}")
    print(f"denied paths: {len(denied)}")
    print(f"injected    : {', '.join(injected) if injected else '(none)'}")
    print(f"ui build    : {ui}")
    print(f"secret scan : {scan}")
    if not args.dry_run:
        print(f"total bytes : {total_bytes:,}")
    print("\n-- sample denied (first 15) --")
    for d in denied[:15]:
        print(f"  - {d}")
    print("\nNext: review the tree, run gitleaks, then push to the public mirror repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
