import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_PASSWORD_HASHER = PasswordHasher()


def normalize_email(value: str) -> str:
    """Return the canonical form used for identity lookup and storage."""
    normalized = value.strip().casefold()
    if not normalized or len(normalized) > 320:
        raise ValueError("email must contain between 1 and 320 characters")
    return normalized


def hash_password(password: str) -> str:
    """Hash a plaintext password with Argon2id."""
    if not password:
        raise ValueError("password must not be empty")
    return _PASSWORD_HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verify a plaintext password without leaking Argon2 exceptions."""
    try:
        return _PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Return whether the persisted hash should be refreshed after login."""
    try:
        return _PASSWORD_HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def generate_session_token() -> str:
    """Generate a cryptographically random opaque session token."""
    return secrets.token_urlsafe(32)


def digest_session_token(token: str) -> bytes:
    """Return the fixed-size digest persisted for a raw session token."""
    return hashlib.sha256(token.encode("utf-8")).digest()
