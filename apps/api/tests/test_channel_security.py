import base64
from uuid import uuid4

import pytest
from pydantic import SecretStr

from customers_manager_hub.channel_models import ChannelCredentialKind
from customers_manager_hub.channel_security import (
    ChannelSecretConfigurationError,
    ChannelSecretDecryptionError,
    decrypt_channel_secret,
    encrypt_channel_secret,
)
from customers_manager_hub.config import Settings


def make_settings(byte: bytes = b"k") -> Settings:
    key = base64.urlsafe_b64encode(byte * 32).decode()
    return Settings(app_env="test", encryption_key=SecretStr(key))


def test_channel_secret_round_trip_is_authenticated_and_not_plaintext() -> None:
    settings = make_settings()
    tenant_id = uuid4()
    account_id = uuid4()
    plaintext = "123456:telegram-bot-token"

    encrypted = encrypt_channel_secret(
        settings,
        tenant_id,
        account_id,
        ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
        plaintext,
    )

    assert plaintext.encode() not in encrypted.ciphertext
    assert len(encrypted.nonce) == 12
    assert (
        decrypt_channel_secret(
            settings,
            tenant_id,
            account_id,
            ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
        )
        == plaintext
    )


def test_channel_secret_rejects_wrong_tenant_account_kind_and_key() -> None:
    settings = make_settings()
    tenant_id = uuid4()
    account_id = uuid4()
    encrypted = encrypt_channel_secret(
        settings,
        tenant_id,
        account_id,
        ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
        "webhook-secret",
    )

    attempts = [
        (make_settings(b"z"), tenant_id, account_id, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET),
        (settings, uuid4(), account_id, ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET),
        (settings, tenant_id, uuid4(), ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET),
        (settings, tenant_id, account_id, ChannelCredentialKind.TELEGRAM_BOT_TOKEN),
    ]
    for candidate_settings, candidate_tenant, candidate_account, candidate_kind in attempts:
        with pytest.raises(ChannelSecretDecryptionError):
            decrypt_channel_secret(
                candidate_settings,
                candidate_tenant,
                candidate_account,
                candidate_kind,
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                key_version=encrypted.key_version,
            )


def test_channel_secret_requires_generated_32_byte_key() -> None:
    missing = Settings(app_env="test")
    with pytest.raises(ChannelSecretConfigurationError):
        encrypt_channel_secret(
            missing,
            uuid4(),
            uuid4(),
            ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            "secret",
        )

    short_key = base64.urlsafe_b64encode(b"short").decode()
    invalid = Settings(app_env="test", encryption_key=SecretStr(short_key))
    with pytest.raises(ChannelSecretConfigurationError):
        encrypt_channel_secret(
            invalid,
            uuid4(),
            uuid4(),
            ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            "secret",
        )
