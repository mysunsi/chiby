"""定位 Assistant 仓库根（含 ``data/`` 与 ``pyproject.toml``）。

packages/ 搬迁后，模块深度变化；禁止再用固定 ``parents[N]`` 指向 data。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def find_repo_root() -> Path:
    here = Path(__file__).resolve().parent
    for p in (here, *here.parents):
        if (p / "pyproject.toml").is_file() and (p / "data").is_dir():
            return p
    # 回落：chibycore → packages → Assistant
    return here.parents[2]
