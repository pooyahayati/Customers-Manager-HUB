import base64
import binascii
import secrets
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from customers_manager_hub.channel_models import ChannelCredentialKind
from customers_manager_hub.config import Settings

_CHANNEL_SECRET_VERSION = 1
_AES_GCM_NONCE_BYTES = 12
_AES_256_KEY_BYTES = 32


class ChannelSecretConfigurationError(RuntimeError):
    pass


class ChannelSecretDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedChannelSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int = _CHANNEL_SECRET_VERSION


def decode_encryption_key(settings: Settings) -> bytes:
    if settings.encryption_key is None:
        raise ChannelSecretConfigurationError("ENCRYPTION_KEY is required for channel credentials")
    encoded = settings.encryption_key.get_secret_value().strip()
    if not encoded or encoded == "change-me":
        raise ChannelSecretConfigurationError("ENCRYPTION_KEY must be a generated 32-byte base64 key")
    try:
        key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ChannelSecretConfigurationError(
            "ENCRYPTION_KEY must be valid URL-safe base64"
        ) from exc
    if len(key) != _AES_256_KEY_BYTES:
        raise ChannelSecretConfigurationError("ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def credential_aad(
    tenant_id: UUID,
    channel_account_id: UUID,
    kind: ChannelCredentialKind,
    key_version: int = _CHANNEL_SECRET_VERSION,
) -> bytes:
    return (
        f"customers-manager-hub:channel-secret:v{key_version}:"
        f"{tenant_id}:{channel_account_id}:{kind.value}"
    ).encode()


def encrypt_channel_secret(
    settings: Settings,
    tenant_id: UUID,
    channel_account_id: UUID,
    kind: ChannelCredentialKind,
    plaintext: str,
) -> EncryptedChannelSecret:
    normalized = plaintext.strip()
    if not normalized:
        raise ValueError("Channel credential must not be blank")
    key = decode_encryption_key(settings)
    nonce = secrets.token_bytes(_AES_GCM_NONCE_BYTES)
    aad = credential_aad(tenant_id, channel_account_id, kind)
    ciphertext = AESGCM(key).encrypt(nonce, normalized.encode(), aad)
    return EncryptedChannelSecret(ciphertext=ciphertext, nonce=nonce)


def decrypt_channel_secret(
    settings: Settings,
    tenant_id: UUID,
    channel_account_id: UUID,
    kind: ChannelCredentialKind,
    *,
    ciphertext: bytes,
    nonce: bytes,
    key_version: int,
) -> str:
    if key_version != _CHANNEL_SECRET_VERSION:
        raise ChannelSecretDecryptionError("Unsupported channel credential key version")
    if len(nonce) != _AES_GCM_NONCE_BYTES:
        raise ChannelSecretDecryptionError("Invalid channel credential nonce")
    key = decode_encryption_key(settings)
    aad = credential_aad(tenant_id, channel_account_id, kind, key_version)
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        return plaintext.decode()
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise ChannelSecretDecryptionError("Channel credential authentication failed") from exc
