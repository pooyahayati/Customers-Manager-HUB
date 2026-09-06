import hashlib
import re
import secrets

from pwdlib import PasswordHash

password_hasher = PasswordHash.recommended()
DUMMY_PASSWORD_HASH = password_hasher.hash("cmh-dummy-login-password")
_slug_pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if len(normalized) > 320 or normalized.count("@") != 1:
        raise ValueError("Invalid email address")
    local_part, domain = normalized.split("@", 1)
    if not local_part or not domain or "." not in domain:
        raise ValueError("Invalid email address")
    return normalized


def normalize_slug(value: str) -> str:
    normalized = value.strip().lower()
    if not _slug_pattern.fullmatch(normalized):
        raise ValueError("Slug must contain only lowercase letters, digits, and internal hyphens")
    return normalized


def validate_new_password(value: str) -> str:
    if len(value) < 12:
        raise ValueError("Password must contain at least 12 characters")
    if len(value) > 1024:
        raise ValueError("Password is too long")
    return value


def hash_password(password: str) -> str:
    return password_hasher.hash(validate_new_password(password))


def verify_password(password: str, encoded_hash: str) -> bool:
    if len(password) > 1024:
        return False
    return password_hasher.verify(password, encoded_hash)


def generate_session_token() -> str:
    """Return an opaque token with at least 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
