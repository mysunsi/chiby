import pytest

from chibycore.host_crypto import decrypt_secret, encrypt_secret


def test_encrypt_roundtrip_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_ENCRYPT_HOST_SECRETS", "1")
    keyf = tmp_path / ".ops_master_key"
    monkeypatch.setattr(
        "chibycore.host_crypto._key_path",
        lambda: keyf,
    )
    a = encrypt_secret("secret123")
    assert a != "secret123"
    assert decrypt_secret(a) == "secret123"


def test_encrypt_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("OPS_ENCRYPT_HOST_SECRETS", raising=False)
    assert encrypt_secret("plain") == "plain"
