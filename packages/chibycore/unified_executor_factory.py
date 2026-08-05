"""由 Host-like 参数构造单次执行器（不向 Web 暴露密码时勿用）。仅运维/服务端内部。"""
from __future__ import annotations

from typing import Any, Protocol, Union

from chibycore.executor_contract import UnifiedExecutor
from chibycore.ssh_oneshot import ParamikoSSHOneShotExecutor
from chibycore.winrm_oneshot import WinRMOneShotExecutor


def winrm_endpoint_url(host: str, port: int, use_ssl: bool) -> str:
    """WinRM 服务端点 URL（文档/诊断用；执行器使用 pypsrp 的 server/port/ssl）。"""
    scheme = "https" if use_ssl else "http"
    return f"{scheme}://{host}:{port}/wsman"


def build_oneshot_from_host_kwargs(
    *,
    conn_type: str,
    host: str,
    port: int,
    username: str,
    password: str | None = None,
    ssh_private_key_path: str | None = None,
    ssh_private_key_passphrase: str | None = None,
    winrm_port: int = 5985,
    winrm_use_ssl: bool = False,
    winrm_transport: str = "ntlm",
    winrm_server_cert_validation: str = "ignore",
) -> UnifiedExecutor:
    ct = (conn_type or "").lower().strip()
    if ct == "ssh":
        ex: UnifiedExecutor = ParamikoSSHOneShotExecutor(
            hostname=host,
            port=int(port),
            username=username,
            password=password,
            pkey_path=ssh_private_key_path or None,
            pkey_pass=ssh_private_key_passphrase or None,
        )
        return ex
    if ct == "winrm":
        return WinRMOneShotExecutor(
            server=host,
            port=int(winrm_port),
            username=username,
            password=password or "",
            ssl=bool(winrm_use_ssl),
            transport=winrm_transport,
            server_cert_validation=winrm_server_cert_validation,
        )
    raise ValueError(f"unsupported conn_type for oneshot: {conn_type}")


class HasHostConnFields(Protocol):
    conn_type: Any
    host: str
    port: int
    username: str
    password: str | None
    winrm_port: int
    winrm_use_ssl: bool
    winrm_transport: str
    winrm_server_cert_validation: str
    ssh_private_key_path: str | None
    ssh_private_key_passphrase: str | None


def build_oneshot_from_pydantic_host(host_obj: Union[HasHostConnFields, Any]) -> UnifiedExecutor:
    ct = getattr(host_obj, "conn_type", "ssh")
    if hasattr(ct, "value"):
        ct = ct.value
    return build_oneshot_from_host_kwargs(
        conn_type=str(ct),
        host=str(host_obj.host),
        port=int(host_obj.port),
        username=str(host_obj.username),
        password=getattr(host_obj, "password", None),
        ssh_private_key_path=getattr(host_obj, "ssh_private_key_path", None),
        ssh_private_key_passphrase=getattr(host_obj, "ssh_private_key_passphrase", None),
        winrm_port=int(getattr(host_obj, "winrm_port", 5985)),
        winrm_use_ssl=bool(getattr(host_obj, "winrm_use_ssl", False)),
        winrm_transport=str(getattr(host_obj, "winrm_transport", "ntlm")),
        winrm_server_cert_validation=str(
            getattr(host_obj, "winrm_server_cert_validation", "ignore")
        ),
    )
