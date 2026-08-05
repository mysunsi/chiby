"""TSM-A T2：SecretStore + ExecTicket。"""

import os
import time

import pytest

from chibycore.exec_ticket import (
    clear_exec_tickets_for_tests,
    command_hash,
    doctor_exec_ticket,
    issue_exec_ticket,
    redeem_exec_ticket,
    revoke_exec_ticket,
    ticket_enforcement_enabled,
)
from chibycore.secret_store import (
    LocalFernetSecretStore,
    VaultSecretStore,
    doctor_secret_store,
)


@pytest.fixture(autouse=True)
def _clean_tickets():
    clear_exec_tickets_for_tests()
    yield
    clear_exec_tickets_for_tests()


def test_command_hash_stable():
    assert command_hash("df -h") == command_hash("  df   -h  ")
    assert command_hash("a") != command_hash("b")


def test_ticket_redeem_ok():
    tid = issue_exec_ticket(turn_id="t1", host_id="h1", command="rm x")
    ok, reason = redeem_exec_ticket(tid, host_id="h1", command="rm x")
    assert ok and reason == "ok"
    ok2, reason2 = redeem_exec_ticket(tid, host_id="h1", command="rm x")
    assert not ok2 and reason2 == "already_used"


def test_ticket_reject_missing_and_mismatch():
    ok, reason = redeem_exec_ticket("", host_id="h1", command="x")
    assert not ok and reason == "missing_ticket"
    tid = issue_exec_ticket(turn_id="t1", host_id="h1", command="rm a")
    ok2, reason2 = redeem_exec_ticket(tid, host_id="h1", command="rm b")
    assert not ok2 and reason2 == "command_hash_mismatch"
    tid2 = issue_exec_ticket(turn_id="t1", host_id="h1", command="rm a")
    ok3, reason3 = redeem_exec_ticket(tid2, host_id="h2", command="rm a")
    assert not ok3 and reason3 == "host_mismatch"


def test_ticket_expired(monkeypatch):
    tid = issue_exec_ticket(turn_id="t1", host_id="h1", command="x", ttl_sec=60)
    import chibycore.exec_ticket as et

    with et._LOCK:
        et._TICKETS[tid].exp = time.time() - 1
    ok, reason = redeem_exec_ticket(tid, host_id="h1", command="x")
    assert not ok and reason == "expired"


def test_ticket_revoked():
    tid = issue_exec_ticket(turn_id="t1", host_id="h1", command="x")
    assert revoke_exec_ticket(tid)
    ok, reason = redeem_exec_ticket(tid, host_id="h1", command="x")
    assert not ok and reason == "revoked"


def test_enforcement_env(monkeypatch):
    monkeypatch.setenv("OPS_TSM_EXEC_TICKET", "0")
    assert not ticket_enforcement_enabled()
    monkeypatch.setenv("OPS_TSM_EXEC_TICKET", "1")
    assert ticket_enforcement_enabled()


def test_local_secret_store():
    class H:
        username = "u"
        password = "secret"
        ssh_private_key_path = ""
        ssh_private_key_passphrase = ""
        conn_type = "ssh"

    store = LocalFernetSecretStore(lambda hid: H() if hid == "h1" else None)
    mat = store.get_host_secret("h1")
    assert mat.has_password
    assert mat.password == "secret"
    assert "secret" not in str(mat.public_dict())
    with pytest.raises(KeyError):
        store.get_host_secret("missing")


def test_vault_fails_closed():
    store = VaultSecretStore()
    with pytest.raises(RuntimeError):
        store.get_host_secret("h1")


def test_doctor_helpers(monkeypatch):
    monkeypatch.delenv("OPS_ENCRYPT_HOST_SECRETS", raising=False)
    d = doctor_secret_store()
    assert d["tsm_layer"] == "L2"
    assert "建议" in (d.get("warn") or "")
    assert doctor_exec_ticket()["enforcement"] in (True, False)
