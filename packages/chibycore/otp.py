"""TSM-A L2/T3 · 动态口令（TOTP）与企业校验占位。

默认关闭。开启：``OPS_TSM_REQUIRE_OTP=1`` 且配置 ``OPS_TSM_OTP_SECRET``（Base32）
或 ``OPS_TSM_OTP_WEBHOOK``（企业校验 URL）。

高危确认卡在 YES 之外叠加 OTP；口令永不写入审计明文。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


def otp_required_globally() -> bool:
    v = (os.environ.get("OPS_TSM_REQUIRE_OTP") or "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def otp_secret() -> str:
    return (os.environ.get("OPS_TSM_OTP_SECRET") or "").strip().replace(" ", "")


def otp_webhook() -> str:
    return (os.environ.get("OPS_TSM_OTP_WEBHOOK") or "").strip()


def otp_configured() -> bool:
    return bool(otp_secret() or otp_webhook())


def should_require_otp(*, risk_level: str = "", require_typed_confirm: bool = False) -> bool:
    """高危（或须 YES）且全局开启且已配置种子/Webhook → 要求 OTP。"""
    if not otp_required_globally():
        return False
    if not otp_configured():
        return False
    risk = (risk_level or "").strip().lower()
    if risk == "high" or require_typed_confirm:
        return True
    return False


def _hotp(key: bytes, counter: int, digits: int = 6) -> str:
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (
        ((digest[offset] & 0x7F) << 24)
        | ((digest[offset + 1] & 0xFF) << 16)
        | ((digest[offset + 2] & 0xFF) << 8)
        | (digest[offset + 3] & 0xFF)
    )
    return str(code % (10**digits)).zfill(digits)


def _decode_base32(secret: str) -> bytes:
    s = secret.upper()
    pad = "=" * ((8 - len(s) % 8) % 8)
    return base64.b32decode(s + pad, casefold=True)


def verify_totp(
    secret: str,
    code: str,
    *,
    window: int = 1,
    step: int = 30,
    digits: int = 6,
    now: Optional[float] = None,
) -> bool:
    raw = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(raw) != digits:
        return False
    try:
        key = _decode_base32(secret)
    except Exception:
        return False
    t = int((now if now is not None else time.time()) // step)
    for w in range(-max(0, window), max(0, window) + 1):
        if hmac.compare_digest(_hotp(key, t + w, digits), raw):
            return True
    return False


def verify_otp_webhook(
    code: str,
    *,
    context: Optional[Dict[str, Any]] = None,
    timeout_sec: float = 3.0,
) -> Tuple[bool, str]:
    """企业 IdP 占位：POST JSON ``{code, context}``，期望 ``{"ok": true}``。"""
    url = otp_webhook()
    if not url:
        return False, "webhook_not_configured"
    body = json.dumps(
        {"code": str(code or "").strip(), "context": context or {}},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=max(0.5, timeout_sec)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw.strip() else {}
            if isinstance(data, dict) and data.get("ok") is True:
                return True, "ok"
            return False, "webhook_denied"
    except Exception as exc:
        logger.warning("otp webhook failed: %s", exc)
        return False, "webhook_error"


def verify_otp_code(
    code: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """优先本地 TOTP；否则 Webhook。均未配置 → 失败安全。"""
    c = (code or "").strip()
    if not c:
        return False, "missing_otp"
    secret = otp_secret()
    if secret:
        if verify_totp(secret, c):
            return True, "totp_ok"
        return False, "totp_mismatch"
    if otp_webhook():
        return verify_otp_webhook(c, context=context)
    return False, "otp_not_configured"


def doctor_otp() -> Dict[str, Any]:
    req = otp_required_globally()
    cfg = otp_configured()
    warn = ""
    if req and not cfg:
        warn = "OPS_TSM_REQUIRE_OTP=1 但未配置 OPS_TSM_OTP_SECRET 或 OPS_TSM_OTP_WEBHOOK"
    return {
        "tsm_layer": "L2",
        "require_otp": req,
        "configured": cfg,
        "mode": (
            "totp"
            if otp_secret()
            else ("webhook" if otp_webhook() else "off")
        ),
        "ok": (not req) or cfg,
        "warn": warn,
    }
