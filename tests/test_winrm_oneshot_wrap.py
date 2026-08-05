"""WinRM 单次执行：LASTEXITCODE 包装解析（无远程依赖）。"""
from chibycore.winrm_oneshot import (
    _parse_exit_marker_from_stdout,
    _wrap_ps_for_last_exit_code,
)


def test_winrm_run_command_timeout_returns_meta(monkeypatch):
    """墙钟超时必须返回 timeout meta，不得无限阻塞。"""
    from chibycore import winrm_oneshot as w

    class _FakeClient:
        wsman = object()

    def _boom(*a, **k):
        raise TimeoutError("WinRM 命令执行超时（>5s）")

    monkeypatch.setattr(w, "_run_psrp_poll_stream", _boom)
    monkeypatch.setattr(w, "Client", object)  # already connected path
    ex = w.WinRMOneShotExecutor("h", 5985, "u", "p")
    ex._client = _FakeClient()
    from chibycore.executor_contract import RunOptions

    r = ex.run_command("Start-Sleep -Seconds 999", RunOptions(timeout_sec=5))
    assert r.exit_code == -1
    assert r.meta.get("timeout") is True
    assert "超时" in (r.stderr or "")


def test_parse_strips_last_marker():
    raw = "line1\r\nline2\r\n__OPS_EXIT_CODE__:42\r\n"
    out, code = _parse_exit_marker_from_stdout(raw)
    assert code == 42
    assert out == "line1\r\nline2"


def test_parse_no_marker():
    out, code = _parse_exit_marker_from_stdout("hello")
    assert code is None
    assert out == "hello"


def test_parse_multiline_uses_last_marker():
    text = "ok\n__OPS_EXIT_CODE__:1\nmore\n__OPS_EXIT_CODE__:9\n"
    out, code = _parse_exit_marker_from_stdout(text)
    assert code == 9
    # 仅剥离「最后一处」标记行；正文若含相同字样会保留（应避免在脚本中打印该前缀）
    assert out == "ok\n__OPS_EXIT_CODE__:1\nmore"
