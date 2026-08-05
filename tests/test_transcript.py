import json

from chibycore import transcript


def test_transcript_append(monkeypatch, tmp_path):
    monkeypatch.setenv("OPS_TRANSCRIPT", "1")
    monkeypatch.setattr(transcript, "_TRANSCRIPT_ROOT", tmp_path)
    transcript.append_transcript("s1", "out", "hello\n")
    transcript.append_transcript("s1", "in", "ls\n")
    p = tmp_path / "s1.jsonl"
    assert p.exists()
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    r0 = json.loads(lines[0])
    assert r0["direction"] == "out"
    assert "hello" in r0["data"]
