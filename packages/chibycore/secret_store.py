"""TSM-A L2 · 凭据保险箱抽象（SecretStore）。

LocalFernet：经 ``host_crypto`` 解密后的主机材料，按需取出。  
Vault：接口占位，未配置时失败安全（拒绝给密）。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol

logger = logging.getLogger(__name__)

HostResolver = Callable[[str], Optional[Any]]


@dataclass
class HostSecretMaterial:
    """短窗口内使用的主机凭据视图（勿写入审计明文）。"""

    host_id: str
    username: str = ""
    has_password: bool = False
    has_private_key: bool = False
    # 仅执行器侧短持；SecretStore 调用方应尽快用完，勿入 ticket / JSONL
    password: str = field(default="", repr=False)
    ssh_private_key_path: str = field(default="", repr=False)
    ssh_private_key_passphrase: str = field(default="", repr=False)
    conn_type: str = "ssh"
    source: str = "local_fernet"  # local_fernet | vault | memory

    def public_dict(self) -> Dict[str, Any]:
        return {
            "host_id": self.host_id,
            "username": self.username,
            "has_password": self.has_password,
            "has_private_key": self.has_private_key,
            "conn_type": self.conn_type,
            "source": self.source,
        }


class SecretStore(Protocol):
    def get_host_secret(self, host_id: str) -> HostSecretMaterial: ...

    def backend_name(self) -> str: ...


def secret_store_backend() -> str:
    return (os.environ.get("OPS_TSM_SECRET_STORE") or "local").strip().lower() or "local"


def encrypt_hosts_enabled() -> bool:
    return os.environ.get("OPS_ENCRYPT_HOST_SECRETS", "").strip() == "1"


class LocalFernetSecretStore:
    """从 HostResolver 取主机；字段若已 ENC$ 则经 host_crypto 解密。"""

    def __init__(self, resolve_host: HostResolver) -> None:
        self._resolve = resolve_host

    def backend_name(self) -> str:
        return "local_fernet"

    def get_host_secret(self, host_id: str) -> HostSecretMaterial:
        from chibycore.host_crypto import decrypt_host_dict

        hid = (host_id or "").strip()
        if not hid:
            raise KeyError("empty_host_id")
        host = self._resolve(hid)
        if host is None:
            raise KeyError(f"host_not_found:{hid}")

        if isinstance(host, dict):
            raw = decrypt_host_dict(dict(host))
            username = str(raw.get("username") or "")
            password = str(raw.get("password") or "")
            key_path = str(raw.get("ssh_private_key_path") or "")
            phrase = str(raw.get("ssh_private_key_passphrase") or "")
            ct = str(raw.get("conn_type") or "ssh")
        else:
            # Pydantic Host：可能已在加载时解密
            username = str(getattr(host, "username", "") or "")
            password = str(getattr(host, "password", "") or "")
            key_path = str(getattr(host, "ssh_private_key_path", "") or "")
            phrase = str(getattr(host, "ssh_private_key_passphrase", "") or "")
            ct = getattr(host, "conn_type", "ssh")
            if hasattr(ct, "value"):
                ct = ct.value
            ct = str(ct or "ssh")
            # 若仍带 ENC$ 前缀，再解一次
            blob = decrypt_host_dict(
                {
                    "password": password,
                    "ssh_private_key_passphrase": phrase,
                }
            )
            password = str(blob.get("password") or "")
            phrase = str(blob.get("ssh_private_key_passphrase") or "")

        return HostSecretMaterial(
            host_id=hid,
            username=username,
            has_password=bool(password),
            has_private_key=bool(key_path),
            password=password,
            ssh_private_key_path=key_path,
            ssh_private_key_passphrase=phrase,
            conn_type=ct,
            source="local_fernet",
        )


class VaultSecretStore:
    """企业 Vault 适配器占位：未配置则失败安全。"""

    def backend_name(self) -> str:
        return "vault"

    def get_host_secret(self, host_id: str) -> HostSecretMaterial:
        addr = (os.environ.get("OPS_VAULT_ADDR") or "").strip()
        if not addr:
            raise RuntimeError("vault_not_configured")
        # 二期：按 host_id 读 KV。当前失败安全。
        raise RuntimeError("vault_not_implemented")


def build_secret_store(resolve_host: HostResolver) -> SecretStore:
    backend = secret_store_backend()
    if backend in ("vault", "hashicorp"):
        return VaultSecretStore()
    return LocalFernetSecretStore(resolve_host)


def doctor_secret_store() -> Dict[str, Any]:
    """供 rehearsal / status：加密开关与后端。"""
    enc = encrypt_hosts_enabled()
    backend = secret_store_backend()
    warn = ""
    if not enc:
        warn = "建议生产设置 OPS_ENCRYPT_HOST_SECRETS=1"
    if backend in ("vault", "hashicorp") and not (
        os.environ.get("OPS_VAULT_ADDR") or ""
    ).strip():
        warn = (warn + "; " if warn else "") + "Vault 后端未配置 OPS_VAULT_ADDR"
    return {
        "backend": backend,
        "encrypt_hosts": enc,
        "ok": backend == "local" or bool((os.environ.get("OPS_VAULT_ADDR") or "").strip()),
        "warn": warn,
        "tsm_layer": "L2",
    }
