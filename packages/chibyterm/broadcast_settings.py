"""群发设置：总体分析报告汇报口吻（持久化 data/broadcast_settings.json）。"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# ops=通用运维；risk=风险合规；capacity=资源容量；strategy=战略决策
REPORT_TONES = ("ops", "risk", "capacity", "strategy")
DEFAULT_REPORT_TONE = "ops"

_TONE_META = {
    "ops": {
        "label_zh": "通用运维汇报",
        "label_zh_tw": "通用運維匯報",
        "label_en": "General ops (leadership)",
    },
    "risk": {
        "label_zh": "风险 / 合规",
        "label_zh_tw": "風險 / 合規",
        "label_en": "Risk / compliance",
    },
    "capacity": {
        "label_zh": "资源容量规划",
        "label_zh_tw": "資源容量規劃",
        "label_en": "Capacity planning",
    },
    "strategy": {
        "label_zh": "战略决策（CEO/CTO）",
        "label_zh_tw": "戰略決策（CEO/CTO）",
        "label_en": "Strategy (CEO/CTO)",
    },
}


def normalize_report_tone(value: Any) -> str:
    t = str(value or "").strip().lower()
    if t in REPORT_TONES:
        return t
    # 兼容别名
    aliases = {
        "default": "ops",
        "executive": "ops",
        "compliance": "risk",
        "security": "risk",
        "resource": "capacity",
        "capacity_planning": "capacity",
        "ceo": "strategy",
        "cto": "strategy",
        "strategic": "strategy",
    }
    return aliases.get(t, DEFAULT_REPORT_TONE)


def _settings_path() -> Path:
    try:
        from chibycore.repo_root import find_repo_root

        return find_repo_root() / "data" / "broadcast_settings.json"
    except Exception:
        return Path.cwd() / "data" / "broadcast_settings.json"


def default_settings() -> Dict[str, Any]:
    return {"report_tone": DEFAULT_REPORT_TONE}


def load_broadcast_settings() -> Dict[str, Any]:
    path = _settings_path()
    with _lock:
        if not path.is_file():
            return default_settings()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("读取 broadcast_settings.json 失败: %s", exc)
            return default_settings()
    if not isinstance(raw, dict):
        return default_settings()
    out = default_settings()
    out["report_tone"] = normalize_report_tone(raw.get("report_tone"))
    return out


def save_broadcast_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    cur = load_broadcast_settings()
    if "report_tone" in patch:
        cur["report_tone"] = normalize_report_tone(patch.get("report_tone"))
    path = _settings_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cur, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return cur


def list_tone_options(ui_locale: str = "zh-CN") -> List[Dict[str, str]]:
    loc = (ui_locale or "zh-CN").strip()
    key = "label_en"
    if loc.startswith("zh") and "TW" in loc.upper():
        key = "label_zh_tw"
    elif loc.startswith("zh"):
        key = "label_zh"
    return [
        {"id": tid, "label": str(_TONE_META[tid][key])}
        for tid in REPORT_TONES
    ]


def tone_label(tone: str, ui_locale: str = "zh-CN") -> str:
    tid = normalize_report_tone(tone)
    meta = _TONE_META[tid]
    loc = (ui_locale or "zh-CN").strip()
    if loc.startswith("zh") and "TW" in loc.upper():
        return str(meta["label_zh_tw"])
    if loc.startswith("zh"):
        return str(meta["label_zh"])
    return str(meta["label_en"])
