"""TSM-A L3 · SIEM 外送（Webhook / 文件尾随）+ 失败重试队列。

不阻塞主路径：``append_mobile_audit`` 后异步尽力投递；失败写入本地重试队列。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_PROJECT_ROOT = __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root()
_DEFAULT_EVENTS = frozenset(
    {
        "permission_allow_exec",
        "permission_deny",
        "permission_typed_confirm_fail",
        "permission_otp_fail",
        "ticket_issue",
        "ticket_redeem",
        "ticket_reject",
        "remote_tool_exec",
        "closure_break",
        "task_status",
        "tsm_l1_text_confirm_blocked",
        "agent_plan",
        "advanced_mutate_allow",
    }
)

_CFG_LOCK = threading.RLock()
_CFG_CACHE: Optional[Dict[str, Any]] = None
_WORKER_STARTED = False


def default_siem_config_path() -> Path:
    return _PROJECT_ROOT / "data" / "mobile_siem.yaml"


def default_siem_retry_path() -> Path:
    return _PROJECT_ROOT / "data" / "mobile_siem_retry.jsonl"


def _load_yaml_or_empty(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        # 极简：支持 KEY: value 行（无 yaml 时）
        out: Dict[str, Any] = {}
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or ":" not in s:
                continue
            k, v = s.split(":", 1)
            out[k.strip()] = v.strip().strip("'\"")
        return out


def load_siem_config(*, force: bool = False) -> Dict[str, Any]:
    global _CFG_CACHE
    with _CFG_LOCK:
        if _CFG_CACHE is not None and not force:
            return dict(_CFG_CACHE)
        cfg: Dict[str, Any] = {
            "enabled": False,
            "webhook_url": "",
            "file_path": "",
            "events": list(_DEFAULT_EVENTS),
            "timeout_sec": 2.0,
            "retry_max": 20,
        }
        file_cfg = _load_yaml_or_empty(default_siem_config_path())
        if file_cfg:
            cfg.update({k: v for k, v in file_cfg.items() if v is not None})
        # 环境变量覆盖
        env_url = (os.environ.get("OPS_TSM_SIEM_WEBHOOK") or "").strip()
        if env_url:
            cfg["webhook_url"] = env_url
            cfg["enabled"] = True
        env_file = (os.environ.get("OPS_TSM_SIEM_FILE") or "").strip()
        if env_file:
            cfg["file_path"] = env_file
            cfg["enabled"] = True
        if str(os.environ.get("OPS_TSM_SIEM_ENABLED") or "").strip() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            cfg["enabled"] = True
        if str(os.environ.get("OPS_TSM_SIEM_ENABLED") or "").strip() in (
            "0",
            "false",
            "no",
            "off",
        ):
            cfg["enabled"] = False
        ev = cfg.get("events")
        if isinstance(ev, str):
            cfg["events"] = [x.strip() for x in ev.split(",") if x.strip()]
        _CFG_CACHE = dict(cfg)
        return dict(cfg)


def siem_event_set(cfg: Optional[Dict[str, Any]] = None) -> Set[str]:
    c = cfg or load_siem_config()
    ev = c.get("events") or list(_DEFAULT_EVENTS)
    if not isinstance(ev, (list, tuple, set)):
        return set(_DEFAULT_EVENTS)
    return {str(x).strip() for x in ev if str(x).strip()}


def _append_retry(rec: Dict[str, Any]) -> None:
    path = default_siem_retry_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("siem retry queue write failed")


def _deliver_one(rec: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    ok = True
    url = str(cfg.get("webhook_url") or "").strip()
    timeout = float(cfg.get("timeout_sec") or 2.0)
    if url:
        body = json.dumps(rec, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=max(0.3, timeout)) as resp:
                if int(getattr(resp, "status", 200) or 200) >= 300:
                    ok = False
        except Exception as exc:
            logger.debug("siem webhook fail: %s", exc)
            ok = False
    fpath = str(cfg.get("file_path") or "").strip()
    if fpath:
        try:
            p = Path(fpath)
            if not p.is_absolute():
                p = _PROJECT_ROOT / p
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("siem file fail: %s", exc)
            ok = False
    if not url and not fpath:
        return True  # nothing to do
    return ok


def emit_siem_event(event: str, payload: Optional[Dict[str, Any]] = None) -> None:
    """尽力投递；失败入重试队列。供 audit 钩子调用。"""
    cfg = load_siem_config()
    if not cfg.get("enabled"):
        return
    ev = str(event or "").strip()
    if ev not in siem_event_set(cfg):
        return
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": ev,
        "payload": payload or {},
        "tsm_model": "TSM-A",
        "source": "assistant_mobile",
    }
    if not (cfg.get("webhook_url") or cfg.get("file_path")):
        return

    def _run() -> None:
        if _deliver_one(rec, cfg):
            return
        retry = dict(rec)
        retry["retry_n"] = 1
        retry["next_ts"] = time.time() + 5
        _append_retry(retry)

    try:
        threading.Thread(target=_run, name="siem-emit", daemon=True).start()
    except Exception:
        try:
            _run()
        except Exception:
            logger.exception("siem emit failed")


def flush_siem_retry_queue(*, max_items: int = 50) -> Dict[str, int]:
    """处理重试队列（可被 doctor / 定时调用）。"""
    path = default_siem_retry_path()
    if not path.is_file():
        return {"ok": 0, "fail": 0, "left": 0}
    cfg = load_siem_config()
    if not cfg.get("enabled"):
        return {"ok": 0, "fail": 0, "left": 0}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"ok": 0, "fail": 0, "left": 0}
    remain: List[str] = []
    ok_n = fail_n = 0
    now = time.time()
    retry_max = int(cfg.get("retry_max") or 20)
    processed = 0
    for line in lines:
        if processed >= max_items:
            remain.append(line)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if float(rec.get("next_ts") or 0) > now:
            remain.append(line)
            continue
        processed += 1
        if _deliver_one(rec, cfg):
            ok_n += 1
            continue
        n = int(rec.get("retry_n") or 1) + 1
        if n > retry_max:
            fail_n += 1
            continue
        rec["retry_n"] = n
        rec["next_ts"] = now + min(300, 5 * n)
        remain.append(json.dumps(rec, ensure_ascii=False))
        fail_n += 1
    try:
        path.write_text("\n".join(remain) + ("\n" if remain else ""), encoding="utf-8")
    except OSError:
        logger.exception("siem retry rewrite failed")
    return {"ok": ok_n, "fail": fail_n, "left": len(remain)}


def doctor_siem() -> Dict[str, Any]:
    cfg = load_siem_config(force=True)
    enabled = bool(cfg.get("enabled"))
    has_sink = bool(cfg.get("webhook_url") or cfg.get("file_path"))
    warn = ""
    if enabled and not has_sink:
        warn = "SIEM 已启用但未配置 webhook_url / file_path / 环境变量"
    return {
        "tsm_layer": "L3",
        "enabled": enabled,
        "has_sink": has_sink,
        "webhook": bool(cfg.get("webhook_url")),
        "file": bool(cfg.get("file_path")),
        "ok": (not enabled) or has_sink,
        "warn": warn,
        "config_path": str(default_siem_config_path()),
    }
