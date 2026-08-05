"""TSM-A L2 · 短时效执行凭证（ExecTicket）。

确认卡 allow 后签发；执行前核销。票内**不含**密码，仅授权 SecretStore 取密一次。
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExecTicket:
    ticket_id: str
    turn_id: str
    host_id: str
    command_hash: str
    exp: float
    scope: str = "once"  # once
    used: bool = False
    revoked: bool = False


_LOCK = threading.RLock()
_TICKETS: Dict[str, ExecTicket] = {}


def ticket_enforcement_enabled() -> bool:
    """默认开启：挂 require_ticket 的路径必须核销成功。

    ``OPS_TSM_EXEC_TICKET=0`` 时仅签发/审计，不因缺票拒绝（观察模式）。
    """
    v = (os.environ.get("OPS_TSM_EXEC_TICKET") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def ticket_ttl_sec() -> int:
    try:
        return max(15, min(int(os.environ.get("OPS_TSM_EXEC_TICKET_TTL") or "120"), 600))
    except ValueError:
        return 120


def command_hash(command: str) -> str:
    norm = " ".join((command or "").strip().split())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]


def issue_exec_ticket(
    *,
    turn_id: str,
    host_id: str,
    command: str,
    ttl_sec: Optional[int] = None,
) -> str:
    """签发一次性短票；返回 ticket_id。"""
    tid = "tkt_" + uuid.uuid4().hex[:16]
    ttl = int(ttl_sec) if ttl_sec is not None else ticket_ttl_sec()
    rec = ExecTicket(
        ticket_id=tid,
        turn_id=(turn_id or "").strip(),
        host_id=(host_id or "").strip(),
        command_hash=command_hash(command),
        exp=time.time() + max(1, ttl),
        scope="once",
    )
    with _LOCK:
        _purge_locked()
        _TICKETS[tid] = rec
    return tid


def revoke_exec_ticket(ticket_id: str) -> bool:
    with _LOCK:
        rec = _TICKETS.get((ticket_id or "").strip())
        if not rec:
            return False
        rec.revoked = True
        return True


def redeem_exec_ticket(
    ticket_id: str,
    *,
    host_id: str,
    command: str,
) -> Tuple[bool, str]:
    """核销。成功则标记 used。返回 (ok, reason)。"""
    tid = (ticket_id or "").strip()
    if not tid:
        return False, "missing_ticket"
    with _LOCK:
        _purge_locked()
        rec = _TICKETS.get(tid)
        if rec is None:
            return False, "unknown_ticket"
        if rec.revoked:
            return False, "revoked"
        if rec.used:
            return False, "already_used"
        if time.time() > rec.exp:
            rec.used = True
            return False, "expired"
        if rec.host_id and host_id and rec.host_id != (host_id or "").strip():
            return False, "host_mismatch"
        ch = command_hash(command)
        if rec.command_hash != ch:
            return False, "command_hash_mismatch"
        rec.used = True
        return True, "ok"


def peek_exec_ticket(ticket_id: str) -> Optional[Dict[str, object]]:
    with _LOCK:
        rec = _TICKETS.get((ticket_id or "").strip())
        if not rec:
            return None
        return {
            "ticket_id": rec.ticket_id,
            "turn_id": rec.turn_id,
            "host_id": rec.host_id,
            "command_hash": rec.command_hash,
            "exp": rec.exp,
            "used": rec.used,
            "revoked": rec.revoked,
            "expired": time.time() > rec.exp,
        }


def clear_exec_tickets_for_tests() -> None:
    with _LOCK:
        _TICKETS.clear()


def _purge_locked() -> None:
    now = time.time()
    # 已用/吊销保留一小段，便于返回 already_used / revoked（而非 unknown）
    dead = [
        k
        for k, v in _TICKETS.items()
        if ((v.used or v.revoked) and now > v.exp + 60)
        or ((not v.used and not v.revoked) and now > v.exp + 300)
    ]
    for k in dead[:200]:
        _TICKETS.pop(k, None)


def doctor_exec_ticket() -> Dict[str, object]:
    return {
        "enforcement": ticket_enforcement_enabled(),
        "ttl_sec": ticket_ttl_sec(),
        "ok": True,
        "tsm_layer": "L2",
        "detail": (
            "OPS_TSM_EXEC_TICKET=1 时，确认卡批准后的执行须核销短票"
        ),
    }
