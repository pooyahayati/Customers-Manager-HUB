import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from customers_manager_hub.channel_security import decode_encryption_key
from customers_manager_hub.config import Settings
from customers_manager_hub.database import AsyncSessionFactory
from customers_manager_hub.platform_ai_models import (
    PlatformAIProvider,
    PlatformAIProviderCredential,
)

_PLATFORM_AI_SECRET_VERSION = 1
_AES_GCM_NONCE_BYTES = 12


class PlatformAISecretDecryptionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EncryptedPlatformAISecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int = _PLATFORM_AI_SECRET_VERSION


def _credential_aad(
    provider: PlatformAIProvider,
    key_version: int = _PLATFORM_AI_SECRET_VERSION,
) -> bytes:
    return f"customers-manager-hub:platform-ai-secret:v{key_version}:{provider.value}".encode()


def encrypt_platform_ai_secret(
    settings: Settings,
    provider: PlatformAIProvider,
    plaintext: str,
) -> EncryptedPlatformAISecret:
    normalized = plaintext.strip()
    if not normalized:
        raise ValueError("AI provider credential must not be blank")
    nonce = secrets.token_bytes(_AES_GCM_NONCE_BYTES)
    ciphertext = AESGCM(decode_encryption_key(settings)).encrypt(
        nonce,
        normalized.encode(),
        _credential_aad(provider),
    )
    return EncryptedPlatformAISecret(ciphertext=ciphertext, nonce=nonce)


def decrypt_platform_ai_secret(
    settings: Settings,
    provider: PlatformAIProvider,
    *,
    ciphertext: bytes,
    nonce: bytes,
    key_version: int,
) -> str:
    if key_version != _PLATFORM_AI_SECRET_VERSION:
        raise PlatformAISecretDecryptionError("Unsupported AI credential key version")
    if len(nonce) != _AES_GCM_NONCE_BYTES:
        raise PlatformAISecretDecryptionError("Invalid AI credential nonce")
    try:
        plaintext = AESGCM(decode_encryption_key(settings)).decrypt(
            nonce,
            ciphertext,
            _credential_aad(provider, key_version),
        )
        return plaintext.decode()
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise PlatformAISecretDecryptionError("AI credential authentication failed") from exc


async def load_platform_ai_secret(
    settings: Settings,
    session_factory: AsyncSessionFactory,
    provider: PlatformAIProvider,
    fallback: str | None = None,
) -> str | None:
    async with session_factory() as db:
        credential = await db.get(PlatformAIProviderCredential, provider.value)
    if credential is None:
        return fallback
    return decrypt_platform_ai_secret(
        settings,
        provider,
        ciphertext=credential.ciphertext,
        nonce=credential.nonce,
        key_version=credential.key_version,
    )
