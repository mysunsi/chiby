"""CDU 服务端 JSON 存储（按助手 / 用户分桶）。"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from chibyterm.context_units.types import UnitValue, user_key_for

logger = logging.getLogger(__name__)

_SAFE_SEG = re.compile(r"^[\w.\-:@]{1,120}$")


def _project_root() -> Path:
    from chibycore.repo_root import find_repo_root

    return find_repo_root()


def default_unit_store_dir() -> Path:
    override = (os.environ.get("OPS_CONTEXT_UNITS_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _project_root() / "data" / "context_units"


def _safe_seg(s: str) -> Optional[str]:
    t = (s or "").strip()
    if not t or not _SAFE_SEG.match(t):
        return None
    if ".." in t or "/" in t or "\\" in t:
        return None
    return t


class ContextUnitStore:
    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or default_unit_store_dir()

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, assistant_id: str, user_key: str, unit_id: str) -> Optional[Path]:
        a = _safe_seg(assistant_id)
        u = _safe_seg(user_key)
        n = _safe_seg(unit_id)
        if not a or not u or not n:
            return None
        return self._root / a / u / f"{n}.json"

    def load(
        self,
        *,
        assistant_id: str,
        unit_id: str,
        external_user_id: Optional[str] = None,
    ) -> Optional[UnitValue]:
        uk = user_key_for(external_user_id)
        path = self.path_for(assistant_id, uk, unit_id)
        if path is None or not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("CDU load failed %s: %s", path, e)
            return None
        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, dict):
            data = {k: v for k, v in raw.items() if k not in ("unit_id", "assistant_id", "user_key", "updated_at")}
        return UnitValue(
            unit_id=str(raw.get("unit_id") or unit_id),
            assistant_id=str(raw.get("assistant_id") or assistant_id),
            user_key=str(raw.get("user_key") or uk),
            data=dict(data),
            updated_at=float(raw.get("updated_at") or 0.0),
        )

    def save(
        self,
        *,
        assistant_id: str,
        unit_id: str,
        data: Dict[str, Any],
        external_user_id: Optional[str] = None,
        updated_at: Optional[float] = None,
    ) -> UnitValue:
        uk = user_key_for(external_user_id)
        path = self.path_for(assistant_id, uk, unit_id)
        if path is None:
            raise ValueError("invalid assistant/user/unit id for CDU path")
        ts = float(updated_at if updated_at is not None else time.time())
        value = UnitValue(
            unit_id=unit_id,
            assistant_id=assistant_id,
            user_key=uk,
            data=dict(data or {}),
            updated_at=ts,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "v": 1,
            "unit_id": value.unit_id,
            "assistant_id": value.assistant_id,
            "user_key": value.user_key,
            "data": value.data,
            "updated_at": value.updated_at,
        }
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".cdu-",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(raw)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return value


_DEFAULT_STORE: Optional[ContextUnitStore] = None


def default_unit_store() -> ContextUnitStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = ContextUnitStore()
    return _DEFAULT_STORE


def reset_default_unit_store_for_tests(store: Optional[ContextUnitStore] = None) -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = store
