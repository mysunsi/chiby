"""开发期包名引导：使 ``uvicorn terminal.main:app`` 在仓库根可直接启动。

真实源码在 ``packages/chibyterm``；本目录仅作过渡别名（P0-7），无业务逻辑。
正式包名为 ``chibyterm``；公开 wheel 不含本 shim。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGES = _ROOT / "packages"
_REAL = _PACKAGES / "chibyterm"

if _PACKAGES.is_dir():
    p = str(_PACKAGES)
    if p not in sys.path:
        sys.path.insert(0, p)

# 子模块（main、models…）从真实包目录加载
__path__ = [str(_REAL)]

__version__ = "0.1.0"
