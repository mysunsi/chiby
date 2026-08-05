"""闭环「数字孪生」可审计回放包（Replay Bundle）：落盘 JSON，供合规复盘与关联追溯。

环境变量：
  OPS_REPLAY_BUNDLE=0 — 关闭写入（默认开启）
  OPS_REPLAY_MASK_HOSTADDR=1 — meta 中不写具体 IP/域名，仅保留 host_id
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from chibycore.redaction import redact_command_text

REPLAY_SCHEMA_VERSION = "1.0"
REPLAY_KIND = "closure_replay_bundle"


def _root() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / "replay_bundles"


def replay_bundle_enabled() -> bool:
    return os.environ.get("OPS_REPLAY_BUNDLE", "1").strip() not in ("0", "false", "no")


def mask_host_address() -> bool:
    return os.environ.get("OPS_REPLAY_MASK_HOSTADDR", "").strip() in ("1", "true", "yes")


@dataclass
class ReplayBundleMeta:
    """一次闭环调用的上下文（不含密钥）。"""

    entrypoint: str
    mirror_session_id: str = ""
    initial_command: str = ""
    nl_intent_hint: Optional[str] = None
    archive_kb: bool = False
    host_id: Optional[str] = None
    host_name: Optional[str] = None
    host_address: Optional[str] = None
    conn_type: Optional[str] = None
    closure_session_id: Optional[str] = None


def _step_command_redacted(step: Dict[str, Any]) -> str:
    return redact_command_text(str(step.get("command") or ""), max_len=16000)


def _normalize_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """为每步增加 command_redacted，便于审计阅读。"""
    out: List[Dict[str, Any]] = []
    for i, st in enumerate(steps):
        d = dict(st)
        d["sequence"] = i
        d["command_redacted"] = _step_command_redacted(d)
        out.append(d)
    return out


def _aggregate(steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    denied = sum(1 for s in steps if not s.get("gateway_allowed"))
    dur = 0
    for s in steps:
        # ClosureStepResponse 不含 duration；若有扩展字段则累加
        ex = s.get("duration_ms")
        if isinstance(ex, (int, float)):
            dur += int(ex)
    return {
        "step_count": len(steps),
        "gateway_denied_steps": denied,
        "total_duration_ms_est": dur,
    }


def _meta_public(meta: ReplayBundleMeta) -> Dict[str, Any]:
    d = asdict(meta)
    if mask_host_address():
        d["host_address"] = None
        d["host_address_masked"] = True
    else:
        d["host_address_masked"] = False
    if d.get("initial_command"):
        d["initial_command_redacted"] = redact_command_text(str(d["initial_command"]), max_len=16000)
    return d


def build_replay_bundle_dict(
    *,
    trace_id: str,
    success_mode: str,
    ok: bool,
    stop_reason: str,
    final_exit_code: Optional[int],
    steps: List[Dict[str, Any]],
    meta: ReplayBundleMeta,
    started_at: Optional[str] = None,
) -> Dict[str, Any]:
    """组装完整回放包（不落盘）。"""
    norm_steps = _normalize_steps(steps)
    now = datetime.now(timezone.utc).isoformat()
    bundle: Dict[str, Any] = {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "kind": REPLAY_KIND,
        "trace_id": trace_id,
        "bundle_generated_at": now,
        "started_at": started_at or now,
        "success_mode": success_mode,
        "ok": ok,
        "stop_reason": stop_reason,
        "final_exit_code": final_exit_code,
        "meta": _meta_public(meta),
        "aggregate": _aggregate(norm_steps),
        "steps": norm_steps,
    }
    return bundle


def save_replay_bundle(bundle: Dict[str, Any]) -> Optional[Path]:
    """原子写入 data/replay_bundles/{trace_id}.json。失败返回 None。"""
    tid = (bundle.get("trace_id") or "").strip()
    if not tid:
        return None
    try:
        root = _root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{tid}.json"
        tmp = root / f".{tid}.json.tmp"
        text = json.dumps(bundle, ensure_ascii=False, indent=2)
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return path
    except OSError:
        return None


def persist_closure_replay_bundle(
    *,
    trace_id: str,
    success_mode: str,
    ok: bool,
    stop_reason: str,
    final_exit_code: Optional[int],
    steps: List[Dict[str, Any]],
    meta: ReplayBundleMeta,
    started_at: Optional[str] = None,
) -> bool:
    if not replay_bundle_enabled():
        return False
    bundle = build_replay_bundle_dict(
        trace_id=trace_id,
        success_mode=success_mode,
        ok=ok,
        stop_reason=stop_reason,
        final_exit_code=final_exit_code,
        steps=steps,
        meta=meta,
        started_at=started_at,
    )
    return save_replay_bundle(bundle) is not None


def load_replay_bundle(trace_id: str) -> Optional[Dict[str, Any]]:
    path = _root() / f"{trace_id.strip()}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_replay_bundle_summaries(*, limit: int = 50) -> List[Dict[str, Any]]:
    """按文件修改时间倒序列出摘要（读取每个文件少量字段）。"""
    root = _root()
    if not root.is_dir():
        return []
    files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
    out: List[Dict[str, Any]] = []
    for path in files[: max(1, min(limit, 500))]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = data.get("meta") or {}
            out.append(
                {
                    "trace_id": data.get("trace_id", path.stem),
                    "bundle_generated_at": data.get("bundle_generated_at"),
                    "ok": data.get("ok"),
                    "stop_reason": data.get("stop_reason"),
                    "entrypoint": meta.get("entrypoint"),
                    "host_id": meta.get("host_id"),
                    "href": f"/api/replay-bundles/{path.stem}",
                }
            )
        except (OSError, json.JSONDecodeError):
            continue
    return out
