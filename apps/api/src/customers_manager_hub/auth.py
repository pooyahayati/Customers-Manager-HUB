from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.config import Settings
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuthSession, PlatformUser
from customers_manager_hub.security import (
    digest_session_token,
    generate_session_token,
    hash_password,
    normalize_email,
    password_needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    email: str


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: PlatformUser
    session: AuthSession


def get_request_settings(request: Request) -> Settings:
    """Return the settings object installed by the application factory."""
    return cast(Settings, request.app.state.settings)


def unauthorized() -> HTTPException:
    """Return the single authentication failure shape used by the API."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication credentials",
    )


async def get_auth_context(request: Request, db: DbSession) -> AuthContext:
    """Resolve the current opaque session and active platform user."""
    settings = get_request_settings(request)
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token is None:
        raise unauthorized()

    now = datetime.now(UTC)
    statement = (
        select(AuthSession, PlatformUser)
        .join(PlatformUser, PlatformUser.id == AuthSession.user_id)
        .where(
            AuthSession.token_digest == digest_session_token(raw_token),
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
            PlatformUser.is_active.is_(True),
        )
    )
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise unauthorized()

    auth_session, user = row
    return AuthContext(user=user, session=auth_session)


CurrentAuth = Annotated[AuthContext, Depends(get_auth_context)]


@router.post("/login", response_model=UserResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: DbSession) -> UserResponse:
    """Authenticate a platform user and create a revocable opaque session."""
    try:
        email = normalize_email(payload.email)
    except ValueError:
        raise unauthorized() from None

    user = (
        await db.execute(select(PlatformUser).where(PlatformUser.email == email))
    ).scalar_one_or_none()

    if user is None or not user.is_active or not verify_password(user.password_hash, payload.password):
        raise unauthorized()

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    settings = get_request_settings(request)
    raw_token = generate_session_token()
    auth_session = AuthSession(
        user_id=user.id,
        token_digest=digest_session_token(raw_token),
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_lifetime_seconds),
    )
    db.add(auth_session)
    await db.commit()

    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_lifetime_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return UserResponse(id=str(user.id), email=user.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, auth: CurrentAuth, db: DbSession) -> None:
    """Revoke the current session immediately and clear its browser cookie."""
    auth.session.revoked_at = datetime.now(UTC)
    await db.commit()

    settings = get_request_settings(request)
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/me", response_model=UserResponse)
async def me(auth: CurrentAuth) -> UserResponse:
    """Return the authenticated platform identity."""
    return UserResponse(id=str(auth.user.id), email=auth.user.email)
