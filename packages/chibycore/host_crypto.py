"""主机凭据磁盘加密（Fernet）。环境变量 OPS_ENCRYPT_HOST_SECRETS=1 启用。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

_PREFIX = "ENC$"

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore


def _key_path() -> Path:
    return __import__("chibycore.repo_root", fromlist=["find_repo_root"]).find_repo_root() / "data" / ".ops_master_key"


def _get_fernet() -> Optional[Any]:
    if not Fernet or os.environ.get("OPS_ENCRYPT_HOST_SECRETS", "").strip() != "1":
        return None
    p = _key_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    raw: bytes
    if p.exists():
        raw = p.read_bytes().strip()
    else:
        raw = Fernet.generate_key()
        try:
            p.write_bytes(raw + b"\n")
            if hasattr(os, "chmod"):
                os.chmod(p, 0o600)
        except OSError:
            return None
    try:
        return Fernet(raw)
    except Exception:
        return None


def encrypt_secret(s: Optional[str]) -> Optional[str]:
    if s is None or s == "":
        return s
    f = _get_fernet()
    if not f:
        return s
    return _PREFIX + f.encrypt(s.encode("utf-8")).decode("ascii")


def decrypt_secret(s: Optional[str]) -> Optional[str]:
    if s is None or not isinstance(s, str) or not s.startswith(_PREFIX):
        return s
    f = _get_fernet()
    if not f:
        return s
    try:
        return f.decrypt(s[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return s


def encrypt_host_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(d)
    for k in ("password", "ssh_private_key_passphrase"):
        if k in out and out[k]:
            out[k] = encrypt_secret(str(out[k]))
    return out


def decrypt_host_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(d)
    for k in ("password", "ssh_private_key_passphrase"):
        if k in out and out[k]:
            out[k] = decrypt_secret(str(out[k]))
    return out
