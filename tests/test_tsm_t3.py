"""TSM-A T3：OTP + SIEM。"""

import json
import time
from pathlib import Path

import pytest

from chibycore.otp import (
    doctor_otp,
    should_require_otp,
    verify_otp_code,
    verify_totp,
    _hotp,
    _decode_base32,
)
from chibycore import otp as otp_mod
from chibycore.siem_sink import (
    doctor_siem,
    emit_siem_event,
    flush_siem_retry_queue,
    load_siem_config,
)


def _gen_totp(secret: str, now: float) -> str:
    key = _decode_base32(secret)
    counter = int(now // 30)
    return _hotp(key, counter)


def test_totp_roundtrip():
    secret = "JBSWY3DPEHPK3PXP"  # "Hello!" classic test secret
    now = 1_700_000_000.0
    code = _gen_totp(secret, now)
    assert verify_totp(secret, code, now=now)
    assert not verify_totp(secret, "000000", now=now)


def test_should_require_otp(monkeypatch):
    monkeypatch.setenv("OPS_TSM_REQUIRE_OTP", "0")
    assert not should_require_otp(risk_level="high", require_typed_confirm=True)
    monkeypatch.setenv("OPS_TSM_REQUIRE_OTP", "1")
    monkeypatch.delenv("OPS_TSM_OTP_SECRET", raising=False)
    monkeypatch.delenv("OPS_TSM_OTP_WEBHOOK", raising=False)
    assert not should_require_otp(risk_level="high", require_typed_confirm=True)
    monkeypatch.setenv("OPS_TSM_OTP_SECRET", "JBSWY3DPEHPK3PXP")
    assert should_require_otp(risk_level="high", require_typed_confirm=True)
    assert not should_require_otp(risk_level="low", require_typed_confirm=False)


def test_verify_otp_code_totp(monkeypatch):
    secret = "JBSWY3DPEHPK3PXP"
    monkeypatch.setenv("OPS_TSM_OTP_SECRET", secret)
    monkeypatch.delenv("OPS_TSM_OTP_WEBHOOK", raising=False)
    now = time.time()
    code = _gen_totp(secret, now)
    # patch time inside verify via now= not available on verify_otp_code; use window
    ok, reason = verify_otp_code(code)
    assert ok and reason == "totp_ok"
    ok2, reason2 = verify_otp_code("111111")
    assert not ok2


def test_doctor_otp(monkeypatch):
    monkeypatch.setenv("OPS_TSM_REQUIRE_OTP", "1")
    monkeypatch.delenv("OPS_TSM_OTP_SECRET", raising=False)
    monkeypatch.delenv("OPS_TSM_OTP_WEBHOOK", raising=False)
    d = doctor_otp()
    assert d["ok"] is False
    assert "未配置" in (d.get("warn") or "")


def test_siem_file_sink(tmp_path, monkeypatch):
    out = tmp_path / "siem.jsonl"
    cfg_path = tmp_path / "mobile_siem.yaml"
    cfg_path.write_text(
        f"enabled: true\nfile_path: {out.as_posix()}\nwebhook_url: ''\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_TSM_SIEM_ENABLED", "1")
    monkeypatch.setenv("OPS_TSM_SIEM_FILE", str(out))
    monkeypatch.delenv("OPS_TSM_SIEM_WEBHOOK", raising=False)

    import chibycore.siem_sink as ss

    ss._CFG_CACHE = None
    monkeypatch.setattr(ss, "default_siem_config_path", lambda: cfg_path)

    emit_siem_event("permission_allow_exec", {"host_id": "h1", "ok": True})
    # thread may be brief
    for _ in range(50):
        if out.is_file() and out.read_text(encoding="utf-8").strip():
            break
        time.sleep(0.02)
    assert out.is_file()
    line = out.read_text(encoding="utf-8").strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["event"] == "permission_allow_exec"
    assert rec["tsm_model"] == "TSM-A"


def test_siem_retry_flush(tmp_path, monkeypatch):
    import chibycore.siem_sink as ss

    out = tmp_path / "siem_out.jsonl"
    retry = tmp_path / "retry.jsonl"
    retry.write_text(
        json.dumps(
            {
                "ts": "t",
                "event": "ticket_reject",
                "payload": {},
                "retry_n": 1,
                "next_ts": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPS_TSM_SIEM_ENABLED", "1")
    monkeypatch.setenv("OPS_TSM_SIEM_FILE", str(out))
    monkeypatch.delenv("OPS_TSM_SIEM_WEBHOOK", raising=False)
    ss._CFG_CACHE = None
    monkeypatch.setattr(ss, "default_siem_retry_path", lambda: retry)
    monkeypatch.setattr(ss, "default_siem_config_path", lambda: tmp_path / "none.yaml")

    stats = flush_siem_retry_queue()
    assert stats["ok"] >= 1
    assert out.is_file()


def test_doctor_siem(monkeypatch):
    monkeypatch.setenv("OPS_TSM_SIEM_ENABLED", "1")
    monkeypatch.delenv("OPS_TSM_SIEM_WEBHOOK", raising=False)
    monkeypatch.delenv("OPS_TSM_SIEM_FILE", raising=False)
    import chibycore.siem_sink as ss

    ss._CFG_CACHE = None
    d = doctor_siem()
    assert d["enabled"] is True
    assert d["ok"] is False
