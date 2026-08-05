import json

from chibycore.audit_log import JsonlAuditLog, reset_audit_log_for_tests


def test_jsonl_append(tmp_path):
    p = tmp_path / "a.jsonl"
    reset_audit_log_for_tests(None)
    log = JsonlAuditLog(p)
    log.append({"event": "test", "x": 1})
    log.append({"event": "test2", "y": "ok"})
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "test"
