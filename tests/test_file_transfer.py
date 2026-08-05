"""聊天文件上下传：路径校验、token 暂存、上传确认。"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from terminal.mobile.file_transfer import (
    DOWNLOAD_STORE_MAX,
    UPLOAD_MAX,
    build_remote_write_b64_command,
    format_attachment_for_agent,
    get_chat_attachment,
    get_download,
    get_pending_upload,
    pop_pending_upload,
    store_chat_attachment,
    store_download,
    store_pending_upload,
    transfers_from_remote_results,
    validate_remote_path,
)


def test_validate_remote_path_rejects_dotdot_and_sensitive():
    assert validate_remote_path("/tmp/a.txt") is None
    assert validate_remote_path("/etc/shadow") is not None
    assert validate_remote_path("/tmp/../etc/passwd") is not None
    assert validate_remote_path("relative.txt") is not None
    assert validate_remote_path(r"C:\temp\a.txt", conn_type="winrm") is None
    assert validate_remote_path(r"C:\Windows\System32\config\SAM", conn_type="winrm") is not None


def test_store_chat_attachment_for_agent():
    aid, err, meta = store_chat_attachment(
        conversation_id="c-att",
        filename="nginx.conf",
        content=b"worker_processes auto;\n",
        external_user_id="u1",
    )
    assert err is None
    assert aid and aid.startswith("att_")
    assert meta and meta["is_text"] is True
    att = get_chat_attachment(aid)
    assert att is not None
    block = format_attachment_for_agent(att)
    assert "【聊天附件】" in block
    assert aid in block
    assert "worker_processes" in block
    assert "ATTACHMENT_BODY" in block
    multi = format_attachment_for_agent(
        att, selected_host_ids=["h1", "h2", "h3"]
    )
    assert "多选了 3 台" in multi
    assert "hosts" in multi
    assert "h1" in multi and "h2" in multi


def test_apply_chat_attachment_to_remote_write():
    from terminal.mobile.remote_tools import (
        RemoteToolCall,
        apply_chat_attachment_to_call,
        remote_tool_call_from_pending_dict,
        remote_tool_call_to_pending_dict,
    )

    aid, err, _ = store_chat_attachment(
        conversation_id="c-w",
        filename="x.txt",
        content=b"hello-attach",
    )
    assert err is None
    call = RemoteToolCall(
        tool="remote_write_file",
        host="h1",
        path="/tmp/x.txt",
        attachment_id=aid,
    )
    resolved, rerr = apply_chat_attachment_to_call(call, conversation_id="c-w")
    assert rerr is None
    assert resolved.content == "hello-attach"
    assert resolved.content_bytes == b"hello-attach"
    pending = remote_tool_call_to_pending_dict(resolved)
    assert pending.get("from_attachment") is True
    assert pending.get("content_b64")
    back = remote_tool_call_from_pending_dict(pending)
    assert back is not None
    assert back.content_bytes == b"hello-attach"


def test_chunked_write_commands_for_large_binary():
    from terminal.mobile.remote_tools import (
        RemoteToolCall,
        apply_chat_attachment_to_call,
        build_chunked_write_commands,
        chunk_b64_chars_for,
        remote_tool_call_to_pending_dict,
    )

    # ~300KB：超过内联 192KB，pending 只挂 attachment_id
    blob = b"\x89PNG\r\n" + (b"\x00\x01\x02\x03" * 75_000)
    aid, err, meta = store_chat_attachment(
        conversation_id="c-bin",
        filename="photo.png",
        content=blob,
    )
    assert err is None
    assert meta and meta["is_text"] is False
    call = RemoteToolCall(
        tool="remote_write_file",
        host="h1",
        path="/home/sunsi/photo.png",
        attachment_id=aid,
    )
    resolved, rerr = apply_chat_attachment_to_call(call, conversation_id="c-bin")
    assert rerr is None
    assert resolved.content_bytes == blob
    assert str(len(blob)) in resolved.shell_text
    pending = remote_tool_call_to_pending_dict(resolved)
    assert pending.get("attachment_id") == aid
    assert not pending.get("content_b64")
    assert pending.get("byte_size") == len(blob)
    cmds, cerr = build_chunked_write_commands(
        "/home/sunsi/photo.png", blob, conn_type="ssh"
    )
    assert cerr is None
    # ~300KB → 64KB 自适应块：init + ~5 chunks + finalize
    assert 3 <= len(cmds) <= 16
    assert any("printf" in c or "cat " in c for c in cmds)
    assert any("base64 -d" in c for c in cmds)
    assert max(len(c) for c in cmds) < 120_000
    # 自适应：小文件 64KB 块；≥1MB 用 256KB 块
    assert chunk_b64_chars_for(500_000) == (64 * 1024 * 4) // 3
    assert chunk_b64_chars_for(1_200_000) == (256 * 1024 * 4) // 3


def test_store_download_token_isolated_by_conversation():
    meta = store_download(
        conversation_id="c1",
        host_id="h1",
        path="/tmp/x.conf",
        content=b"hello-nginx",
        external_user_id="u1",
    )
    assert meta is not None
    assert meta["token"].startswith("ft_")
    assert meta["size"] == 11
    item = get_download(meta["token"])
    assert item is not None
    assert item.conversation_id == "c1"
    assert item.content == b"hello-nginx"
    assert item.filename == "x.conf"


def test_store_download_truncates_over_limit():
    raw = b"x" * (DOWNLOAD_STORE_MAX + 50)
    meta = store_download(
        conversation_id="c2",
        host_id="h1",
        path="/tmp/big.bin",
        content=raw,
    )
    assert meta is not None
    assert meta["truncated"] is True
    assert meta["size"] == DOWNLOAD_STORE_MAX


def test_transfers_from_remote_read_results():
    results = [
        SimpleNamespace(
            tool="remote_read_file",
            ok=True,
            stdout="server {\n  listen 80;\n}\n",
            host="host-a",
            command="remote_read_file /etc/nginx/nginx.conf",
            data={"path": "/etc/nginx/nginx.conf"},
        ),
        SimpleNamespace(
            tool="remote_write_file",
            ok=True,
            stdout="wrote",
            host="host-a",
            command="",
            data={},
        ),
    ]
    out = transfers_from_remote_results(
        results, conversation_id="conv-ft", external_user_id="u"
    )
    assert len(out) == 1
    assert out[0]["host_id"] == "host-a"
    assert out[0]["path"] == "/etc/nginx/nginx.conf"
    assert get_download(out[0]["token"]).content.startswith(b"server")


def test_pending_upload_reject_oversized():
    pid, err, meta = store_pending_upload(
        conversation_id="c",
        host_id="h",
        remote_path="/tmp/a.bin",
        filename="a.bin",
        content=b"z" * (UPLOAD_MAX + 1),
    )
    assert pid is None
    assert err and "过大" in err
    assert meta is None


def test_pending_upload_pop_and_write_cmd():
    data = b"attach-payload-ok"
    pid, err, meta = store_pending_upload(
        conversation_id="c-up",
        host_id="h1",
        remote_path="/tmp/chiby_test.txt",
        filename="chiby_test.txt",
        content=data,
        external_user_id="u1",
    )
    assert err is None
    assert pid and meta and meta["from_attachment"] is True
    assert get_pending_upload(pid) is not None
    item = pop_pending_upload(pid)
    assert item is not None
    assert item.content == data
    assert get_pending_upload(pid) is None
    cmd, cerr = build_remote_write_b64_command(
        item.remote_path, item.content, conn_type="ssh"
    )
    assert cerr is None
    assert "base64" in cmd
    assert base64.b64encode(data).decode("ascii") in cmd


@pytest.mark.asyncio
async def test_file_commit_upload_allow_and_deny(monkeypatch):
    from terminal.mobile import orchestrator as orch_mod
    from terminal.mobile.orchestrator import MobileSessionOrchestrator

    class FakeAcl:
        def allowed_host_ids(self, uid):
            return {"*"}

    class FakeExec:
        def __init__(self):
            self.cmds = []

        async def run(self, host_id, cmd, timeout_sec=60.0, trace_id=""):
            self.cmds.append((host_id, cmd))
            return SimpleNamespace(ok=True, error="", stderr_tail="", stdout_tail="wrote")

    class FakeOrch:
        def __init__(self):
            self._acl = FakeAcl()
            self._executor = FakeExec()
            self._conversations = {}

        def _visible_hosts(self, uid):
            return [SimpleNamespace(id="h1", conn_type="ssh")]

        def _conn_type_for(self, hid, hosts):
            return "ssh"

        file_prepare_upload = MobileSessionOrchestrator.file_prepare_upload
        file_commit_upload = MobileSessionOrchestrator.file_commit_upload

    o = FakeOrch()
    audits = []

    def _audit(kind, payload=None, **kwargs):
        audits.append((kind, payload or {}))

    monkeypatch.setattr(orch_mod, "append_mobile_audit", _audit)

    prep = await o.file_prepare_upload(
        conversation_id="c1",
        external_user_id="u1",
        host_id="h1",
        remote_path="/tmp/up.txt",
        filename="up.txt",
        content=b"hi",
    )
    assert prep["ok"] is True
    pid = prep["pending_id"]

    denied = await o.file_commit_upload(
        conversation_id="c1",
        external_user_id="u1",
        pending_id=pid,
        allow=False,
    )
    assert denied.get("cancelled") is True
    assert any(k == "file_upload" and p.get("allowed") is False for k, p in audits)

    prep2 = await o.file_prepare_upload(
        conversation_id="c1",
        external_user_id="u1",
        host_id="h1",
        remote_path="/tmp/up2.txt",
        filename="up2.txt",
        content=b"hi2",
    )
    allowed = await o.file_commit_upload(
        conversation_id="c1",
        external_user_id="u1",
        pending_id=prep2["pending_id"],
        allow=True,
    )
    assert allowed["ok"] is True
    assert o._executor.cmds
    assert any(
        k == "file_upload" and p.get("allowed") is True and p.get("from_attachment")
        for k, p in audits
    )


@pytest.mark.asyncio
async def test_file_download_by_token_acl(monkeypatch):
    from terminal.mobile import orchestrator as orch_mod
    from terminal.mobile.orchestrator import MobileSessionOrchestrator

    meta = store_download(
        conversation_id="c-dl",
        host_id="h-secret",
        path="/tmp/a.txt",
        content=b"secret-bytes",
        external_user_id="u1",
    )

    class AclDeny:
        def allowed_host_ids(self, uid):
            return {"other"}

    class FakeOrch:
        def __init__(self):
            self._acl = AclDeny()
            self._conversations = {}

        file_download_by_token = MobileSessionOrchestrator.file_download_by_token

    monkeypatch.setattr(orch_mod, "append_mobile_audit", lambda *a, **k: None)
    o = FakeOrch()
    bad = await o.file_download_by_token(
        token=meta["token"],
        conversation_id="c-dl",
        external_user_id="u1",
    )
    assert bad["ok"] is False
    assert "无权" in bad["error"]
