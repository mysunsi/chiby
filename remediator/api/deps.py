"""依赖注入：API Key、环境快照。"""
from __future__ import annotations

import os
import platform
from typing import Optional

try:
    from typing import Annotated
except ImportError:  # Python < 3.9
    from typing_extensions import Annotated

from fastapi import Header, HTTPException

from remediator.api.schemas import RemediateRequest
from remediator.remediation.models import EnvironmentSnapshot


def _expected_api_key() -> str:
    return os.getenv("MY_PROJECT_API_KEY", "YOUR_SECRET_API_KEY").strip()


async def verify_api_key(
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> str:
    """校验 ``X-API-Key``；可通过环境变量 ``MY_PROJECT_API_KEY`` 覆盖默认值。"""
    expected = _expected_api_key()
    if not x_api_key or x_api_key.strip() != expected:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key.strip()


def build_environment_snapshot(request: RemediateRequest) -> EnvironmentSnapshot:
    """由请求构造 EnvironmentSnapshot（含 environment_id，供 KB/LLM 扩展使用）。"""
    root = False
    try:
        root = os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        root = False
    return EnvironmentSnapshot(
        os_name=platform.system() or "",
        os_version=platform.release() or "",
        shell=os.environ.get("SHELL") or os.environ.get("COMSPEC", ""),
        current_user=os.environ.get("USER") or os.environ.get("USERNAME", ""),
        is_root_or_sudo=root,
        cwd=request.cwd or ".",
        extra={"environment_id": request.environment_id},
    )


def get_env_id(environment_id: str) -> str:
    """预留：环境白名单等。"""
    return environment_id
