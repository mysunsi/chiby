"""变更窗口（冻结期）：与策略引擎并行，在网关层将「冻结时段内的执行」转为待审批而非硬拒绝。

配置文件：data/change_window.json（可选）
环境变量：
  OPS_CHANGE_WINDOW_ENABLED=1 — 启用（未设置配置文件 enabled 时也可用）
  OPS_CHANGE_WINDOW_BYPASS=1 — 单次进程内全局绕过（测试用；正式审批请用 ExecutionRequest.change_window_bypass）

JSON 示例见文档字符串下方。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _project_data_path() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data"


def _config_path() -> Path:
    return _project_data_path() / "change_window.json"


@dataclass
class ChangeWindowConfig:
    enabled: bool = False
    timezone: str = "UTC"
    weekly_freeze: List[Dict[str, Any]] = None  # noqa
    daily_freeze: List[Dict[str, Any]] = None  # noqa

    def __post_init__(self):
        if self.weekly_freeze is None:
            self.weekly_freeze = []
        if self.daily_freeze is None:
            self.daily_freeze = []


_cfg_cache: Optional[ChangeWindowConfig] = None
_cfg_mtime: float = 0.0


def change_window_enabled_globally() -> bool:
    env = (os.environ.get("OPS_CHANGE_WINDOW_ENABLED") or "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    cfg = load_change_window_config()
    return bool(cfg.enabled)


def env_bypass_all() -> bool:
    return (os.environ.get("OPS_CHANGE_WINDOW_BYPASS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def load_change_window_config(force: bool = False) -> ChangeWindowConfig:
    """加载 change_window.json；文件不存在则返回默认（禁用）。"""
    global _cfg_cache, _cfg_mtime
    path = _config_path()
    if not path.is_file():
        return ChangeWindowConfig(enabled=False)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ChangeWindowConfig(enabled=False)
    if not force and _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("change_window.json 读取失败: %s", e)
        return ChangeWindowConfig(enabled=False)
    cfg = ChangeWindowConfig(
        enabled=bool(raw.get("enabled")),
        timezone=str(raw.get("timezone") or "UTC"),
        weekly_freeze=list(raw.get("weekly_freeze") or raw.get("freeze_weekly") or []),
        daily_freeze=list(raw.get("daily_freeze") or []),
    )
    _cfg_cache = cfg
    _cfg_mtime = mtime
    return cfg


def _tz():
    try:
        from zoneinfo import ZoneInfo

        tzname = load_change_window_config().timezone or "UTC"
        return ZoneInfo(tzname)
    except Exception:
        from datetime import timezone as tzutc

        return tzutc.utc


def _parse_hm(s: str) -> Tuple[int, int]:
    parts = (s or "").strip().split(":")
    h = int(parts[0]) if parts else 0
    m = int(parts[1]) if len(parts) > 1 else 0
    return max(0, min(23, h)), max(0, min(59, m))


def _now_local() -> datetime:
    return datetime.now(_tz())


def _seconds_since_midnight(dt: datetime) -> int:
    t = dt.timetz().replace(tzinfo=None)
    return t.hour * 3600 + t.minute * 60 + t.second


def _time_in_span(sec: int, start_sec: int, end_sec: int) -> bool:
    """闭区间语义；跨午夜时 start_sec > end_sec。"""
    if start_sec <= end_sec:
        return start_sec <= sec <= end_sec
    return sec >= start_sec or sec <= end_sec


def is_change_window_frozen(now: Optional[datetime] = None) -> bool:
    """
    当前是否处于「冻结窗口」（在此窗口内下发的自动化执行应转待审批）。
    """
    if env_bypass_all():
        return False
    if not change_window_enabled_globally():
        return False

    dt = now or _now_local()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_tz())

    cfg = load_change_window_config()
    sec = _seconds_since_midnight(dt)
    wd = dt.weekday()  # 0=周一 .. 6=周日

    for rule in cfg.daily_freeze:
        sh, sm = _parse_hm(str(rule.get("start") or "00:00"))
        eh, em = _parse_hm(str(rule.get("end") or "23:59"))
        ss = sh * 3600 + sm * 60
        es = eh * 3600 + em * 60
        if _time_in_span(sec, ss, es):
            return True

    for rule in cfg.weekly_freeze:
        days = rule.get("days") or rule.get("weekdays")
        if not days:
            continue
        day_set = set(int(d) % 7 for d in days)
        # 配置约定：0=周一 … 6=周日（与 weekday() 一致）
        if wd not in day_set:
            continue
        sh, sm = _parse_hm(str(rule.get("start") or "00:00"))
        eh, em = _parse_hm(str(rule.get("end") or "23:59"))
        ss = sh * 3600 + sm * 60
        es = eh * 3600 + em * 60
        if _time_in_span(sec, ss, es):
            return True

    return False


def freeze_status_payload() -> Dict[str, Any]:
    """供 GET /api/change-window 展示。"""
    cfg = load_change_window_config()
    frozen = is_change_window_frozen()
    return {
        "enabled": change_window_enabled_globally(),
        "config_enabled": cfg.enabled,
        "timezone": cfg.timezone,
        "frozen_now": frozen,
        "weekly_freeze": cfg.weekly_freeze,
        "daily_freeze": cfg.daily_freeze,
        "local_time": _now_local().isoformat(),
    }


"""
change_window.json 示例：

{
  "enabled": true,
  "timezone": "Asia/Shanghai",
  "daily_freeze": [
    { "start": "09:00", "end": "18:00" }
  ],
  "weekly_freeze": [
    { "days": [5, 6], "start": "22:00", "end": "06:00" }
  ]
}

说明：
- daily_freeze：每日重复的时间段（可覆盖「工作时段禁止变更」等）。
- weekly_freeze：指定 weekday 上的时间段；跨天时 end < start 表示过夜。
"""
