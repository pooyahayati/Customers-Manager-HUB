import secrets
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from customers_manager_hub.channel_security import decode_encryption_key
from customers_manager_hub.config import Settings
from customers_manager_hub.tool_models import ToolAuthType

_TOOL_SECRET_VERSION = 1
_AES_GCM_NONCE_BYTES = 12


class ToolSecretDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedToolSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int = _TOOL_SECRET_VERSION


def tool_credential_aad(
    tenant_id: UUID,
    tool_id: UUID,
    auth_type: ToolAuthType,
    header_name: str | None,
    key_version: int = _TOOL_SECRET_VERSION,
) -> bytes:
    normalized_header = (header_name or "").strip().casefold()
    return (
        f"customers-manager-hub:tool-secret:v{key_version}:"
        f"{tenant_id}:{tool_id}:{auth_type.value}:{normalized_header}"
    ).encode()


def encrypt_tool_secret(
    settings: Settings,
    tenant_id: UUID,
    tool_id: UUID,
    auth_type: ToolAuthType,
    plaintext: str,
    *,
    header_name: str | None = None,
) -> EncryptedToolSecret:
    normalized = plaintext.strip()
    if not normalized:
        raise ValueError("Tool credential must not be blank")
    key = decode_encryption_key(settings)
    nonce = secrets.token_bytes(_AES_GCM_NONCE_BYTES)
    aad = tool_credential_aad(tenant_id, tool_id, auth_type, header_name)
    ciphertext = AESGCM(key).encrypt(nonce, normalized.encode(), aad)
    return EncryptedToolSecret(ciphertext=ciphertext, nonce=nonce)


def decrypt_tool_secret(
    settings: Settings,
    tenant_id: UUID,
    tool_id: UUID,
    auth_type: ToolAuthType,
    *,
    header_name: str | None,
    ciphertext: bytes,
    nonce: bytes,
    key_version: int,
) -> str:
    if key_version != _TOOL_SECRET_VERSION:
        raise ToolSecretDecryptionError("Unsupported tool credential key version")
    if len(nonce) != _AES_GCM_NONCE_BYTES:
        raise ToolSecretDecryptionError("Invalid tool credential nonce")
    key = decode_encryption_key(settings)
    aad = tool_credential_aad(tenant_id, tool_id, auth_type, header_name, key_version)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        return plaintext.decode()
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise ToolSecretDecryptionError("Tool credential authentication failed") from exc
