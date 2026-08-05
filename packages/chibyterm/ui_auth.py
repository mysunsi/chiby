"""ChibyTerm Web UI 简易登录（默认 admin/admin，凭证存 data/ui_auth.json）。"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

COOKIE_NAME = "chibyterm_ui_session"
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
_SESSION_TTL_SEC = 7 * 24 * 3600

_lock = threading.Lock()
_sessions: Dict[str, Dict[str, Any]] = {}  # token -> {user, exp}


def _auth_file(project_root: Path) -> Path:
    return project_root / "data" / "ui_auth.json"


def _hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def _ensure_store(project_root: Path) -> Dict[str, Any]:
    path = _auth_file(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("username") and data.get("salt") and data.get("password_hash"):
                return data
        except Exception as ex:
            logger.warning("读取 ui_auth.json 失败，将重建默认凭证: %s", ex)
    salt = secrets.token_hex(16)
    data = {
        "username": DEFAULT_USERNAME,
        "salt": salt,
        "password_hash": _hash_password(DEFAULT_PASSWORD, salt),
        "updated_at": time.time(),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("已初始化 UI 登录凭证（默认 %s/%s）→ %s", DEFAULT_USERNAME, DEFAULT_PASSWORD, path)
    return data


def load_auth(project_root: Path) -> Dict[str, Any]:
    with _lock:
        return dict(_ensure_store(project_root))


def verify_login(project_root: Path, username: str, password: str) -> bool:
    data = load_auth(project_root)
    if (username or "").strip() != str(data.get("username") or ""):
        return False
    salt = str(data.get("salt") or "")
    expect = str(data.get("password_hash") or "")
    got = _hash_password(password or "", salt)
    return hmac.compare_digest(got, expect)


def change_password(
    project_root: Path,
    username: str,
    current_password: str,
    new_password: str,
) -> Tuple[bool, str]:
    new_password = (new_password or "").strip()
    if len(new_password) < 4:
        return False, "新密码至少 4 位"
    if not verify_login(project_root, username, current_password):
        return False, "当前用户名或密码不正确"
    salt = secrets.token_hex(16)
    data = {
        "username": (username or "").strip() or DEFAULT_USERNAME,
        "salt": salt,
        "password_hash": _hash_password(new_password, salt),
        "updated_at": time.time(),
    }
    path = _auth_file(project_root)
    with _lock:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 改密后使旧会话失效
        _sessions.clear()
    return True, "密码已更新，请重新登录"


def create_session(username: str) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        _sessions[token] = {
            "user": username,
            "exp": time.time() + _SESSION_TTL_SEC,
        }
    return token


def destroy_session(token: Optional[str]) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def session_user(token: Optional[str]) -> Optional[str]:
    if not token:
        return None
    with _lock:
        info = _sessions.get(token)
        if not info:
            return None
        if float(info.get("exp") or 0) < time.time():
            _sessions.pop(token, None)
            return None
        return str(info.get("user") or "") or None


def ui_auth_enabled() -> bool:
    v = (os.environ.get("OPS_UI_AUTH") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")
