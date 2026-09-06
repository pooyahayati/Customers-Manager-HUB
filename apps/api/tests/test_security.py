from customers_manager_hub.security import (
    generate_session_token,
    hash_password,
    hash_session_token,
    normalize_email,
    normalize_slug,
    verify_password,
)


def test_password_hash_is_one_way_and_verifiable() -> None:
    password = "correct horse battery staple"
    encoded = hash_password(password)

    assert encoded != password
    assert verify_password(password, encoded)
    assert not verify_password("wrong password", encoded)


def test_session_token_hash_does_not_contain_raw_token() -> None:
    token = generate_session_token()
    digest = hash_session_token(token)

    assert len(token) >= 32
    assert len(digest) == 64
    assert token not in digest
    assert digest == hash_session_token(token)


def test_identity_normalization_is_deterministic() -> None:
    assert normalize_email("  OWNER@Example.COM ") == "owner@example.com"
    assert normalize_slug("acme-store") == "acme-store"
