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
    generate_session_token,
    hash_session_token,
    normalize_email,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
SESSION_COOKIE_NAME = "cmh_session"
SESSION_TTL = timedelta(days=7)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    id: str
    email: str


@dataclass(frozen=True, slots=True)
class CurrentAuth:
    user: PlatformUser
    session: AuthSession


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


async def get_current_auth(request: Request, db: DbSession) -> CurrentAuth:
    raw_token = request.cookies.get(SESSION_COOKIE_NAME)
    if raw_token is None:
        raise unauthorized()

    statement = (
        select(AuthSession, PlatformUser)
        .join(PlatformUser, PlatformUser.id == AuthSession.user_id)
        .where(AuthSession.token_hash == hash_session_token(raw_token))
    )
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise unauthorized()

    auth_session, user = row
    now = datetime.now(UTC)
    if auth_session.revoked_at is not None or auth_session.expires_at <= now or not user.is_active:
        raise unauthorized()

    return CurrentAuth(user=user, session=auth_session)


CurrentAuthDependency = Annotated[CurrentAuth, Depends(get_current_auth)]


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: DbSession,
) -> UserResponse:
    try:
        email = normalize_email(payload.email)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        ) from None

    user = await db.scalar(
        select(PlatformUser).where(
            PlatformUser.email == email,
            PlatformUser.is_active.is_(True),
        )
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    raw_token = generate_session_token()
    now = datetime.now(UTC)
    auth_session = AuthSession(
        user_id=user.id,
        token_hash=hash_session_token(raw_token),
        expires_at=now + SESSION_TTL,
    )
    db.add(auth_session)
    await db.commit()

    settings = cast(Settings, request.app.state.settings)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.app_env not in {"development", "test"},
        samesite="strict",
        path="/",
    )
    return UserResponse(id=str(user.id), email=user.email)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    current: CurrentAuthDependency,
    db: DbSession,
) -> None:
    current.session.revoked_at = datetime.now(UTC)
    await db.commit()
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        httponly=True,
        samesite="strict",
        path="/",
    )


@router.get("/me", response_model=UserResponse)
async def me(current: CurrentAuthDependency) -> UserResponse:
    return UserResponse(id=str(current.user.id), email=current.user.email)
