"""开发期包名别名：``import terminal`` → ``chibyterm``（P0-7 / P1）。

在未 ``pip install -e .`` 时，把 ``packages/`` 与闭源 ``src/`` 加入
``sys.path``，并注册 meta_path 别名。可在脚本开头::

    import path_alias  # noqa: F401
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
for _rel in (
    "packages",
    "proprietary/chiby_mobile/src",
    "proprietary/chiby_hermes_bridge/src",
):
    _p = _ROOT / _rel
    if _p.is_dir():
        s = str(_p)
        if s not in sys.path:
            sys.path.insert(0, s)

import conftest as _c  # noqa: F401, E402
