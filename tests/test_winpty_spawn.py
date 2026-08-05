"""pywinpty spawn 兼容（1.x encoding / 2.x 无 encoding）。"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from terminal.session_manager import _spawn_winpty_process


def test_spawn_omits_encoding_when_unsupported():
    """pywinpty 2.x：不传 encoding。"""
    mock_spawn = MagicMock(return_value="proc")
    mock_cls = MagicMock()
    mock_cls.spawn = mock_spawn
    mock_cls.spawn.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("argv", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("dimensions", inspect.Parameter.KEYWORD_ONLY, default=(24, 80)),
        ],
    )

    out = _spawn_winpty_process(mock_cls, ["cmd"], height=30, width=100)

    assert out == "proc"
    mock_spawn.assert_called_once_with(["cmd"], dimensions=(30, 100))


def test_spawn_passes_encoding_when_supported():
    """pywinpty 1.x：保留 utf-8 encoding。"""
    mock_spawn = MagicMock(return_value="proc")
    mock_cls = MagicMock()
    mock_cls.spawn = mock_spawn
    mock_cls.spawn.__signature__ = inspect.Signature(
        parameters=[
            inspect.Parameter("argv", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            inspect.Parameter("dimensions", inspect.Parameter.KEYWORD_ONLY, default=(24, 80)),
            inspect.Parameter("encoding", inspect.Parameter.KEYWORD_ONLY, default=None),
        ],
    )

    _spawn_winpty_process(mock_cls, ["pwsh"], height=24, width=80)

    mock_spawn.assert_called_once_with(
        ["pwsh"],
        dimensions=(24, 80),
        encoding="utf-8",
    )


@pytest.mark.skipif(
    __import__("sys").platform != "win32",
    reason="仅 Windows 可测真实 pywinpty",
)
def test_spawn_real_pywinpty_no_typeerror():
    pytest.importorskip("winpty")
    from winpty import PtyProcess

    proc = _spawn_winpty_process(PtyProcess, ["cmd", "/c", "exit", "0"], height=24, width=80)
    try:
        assert proc.isalive()
    finally:
        proc.terminate()
