from pathlib import Path

from chibycore.redaction import redact_command_text, redact_payload
from terminal.mobile.audit import append_mobile_audit, query_mobile_audit
from terminal.mobile.remote_tools import RemoteToolResult
from terminal.mobile.transcript import append_mobile_transcript, read_mobile_transcript


def test_redact_password_pair():
    s = redact_command_text("mysql -u root --password=secret123 -e select 1")
    assert "secret123" not in s
    assert "***" in s


def test_redact_bearer():
    s = redact_command_text("curl -H 'Authorization: Bearer abc.def.ghi' http://x")
    assert "abc.def.ghi" not in s


def test_redact_pem_and_mysql_p():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEsecret\n-----END RSA PRIVATE KEY-----"
    s = redact_command_text(pem)
    assert "MIIEsecret" not in s
    assert "***" in s
    s2 = redact_command_text("mysqldump -uroot -pSuperSecret db")
    assert "SuperSecret" not in s2


def test_redact_payload_nested():
    p = redact_payload(
        {
            "command": "echo password=hunter2",
            "nested": {"stdout": "token=abc123"},
            "password": "should-hide",
            "host_id": "h1",
        }
    )
    assert p["password"] == "***"
    assert "hunter2" not in p["command"]
    assert "abc123" not in p["nested"]["stdout"]
    assert p["host_id"] == "h1"


def test_to_public_dict_redacts_stdout():
    r = RemoteToolResult(
        tool="ssh_execute",
        ok=True,
        command="cat /tmp/x",
        stdout="password=leakme\nok",
        stderr="Bearer tok.xxx.yyy",
    )
    d = r.to_public_dict()
    assert "leakme" not in d["stdout"]
    assert "tok.xxx.yyy" not in d["stderr"]
    assert "***" in d["stdout"]


def test_mobile_audit_redacts(tmp_path: Path):
    p = tmp_path / "a.jsonl"
    append_mobile_audit(
        "exec_done",
        payload={"command": "login password=s3cret", "host_id": "h1"},
        path=p,
    )
    rows = query_mobile_audit(limit=10, path=p)
    assert len(rows) == 1
    assert "s3cret" not in rows[0]["payload"]["command"]


def test_mobile_transcript_redacts(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPS_MOBILE_TRANSCRIPT", "1")
    monkeypatch.setattr(
        "terminal.mobile.transcript.transcript_root",
        lambda: tmp_path / "tr",
    )
    append_mobile_transcript(
        "c-redact",
        "tool_step",
        "ran with password=nope",
        turn_id="tur_1",
    )
    rows = read_mobile_transcript("c-redact", limit=5)
    assert rows
    assert "nope" not in rows[0]["text"]
