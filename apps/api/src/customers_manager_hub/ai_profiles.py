import json
from typing import Annotated, Self, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.ai_models import AITaskProfile, AITaskRoute, AITaskType
from customers_manager_hub.ai_providers import LIVE_AI_PROVIDER_KEYS
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent, TenantRole
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}/ai/task-profiles", tags=["ai"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
WriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
_PARAMETER_LIMIT_BYTES = 8 * 1024
_SENSITIVE_PARAMETER_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credentials",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def validate_safe_parameters(value: dict[str, object]) -> dict[str, object]:
    def visit(node: object) -> None:
        if isinstance(node, dict):
            mapping = cast(dict[object, object], node)
            for raw_key, child in mapping.items():
                if not isinstance(raw_key, str):
                    raise ValueError("AI route parameter keys must be strings")
                if raw_key.strip().casefold() in _SENSITIVE_PARAMETER_KEYS:
                    raise ValueError(f"Sensitive AI route parameter key is not allowed: {raw_key}")
                visit(child)
        elif isinstance(node, list):
            for child in cast(list[object], node):
                visit(child)

    visit(value)
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _PARAMETER_LIMIT_BYTES:
        raise ValueError("AI route parameters are too large")
    return value


class AITaskRouteInput(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    model_id: str = Field(min_length=1, max_length=255)
    parameters: dict[str, object] = Field(default_factory=dict)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in LIVE_AI_PROVIDER_KEYS:
            raise ValueError(f"Unsupported AI provider: {normalized}")
        return normalized

    @field_validator("model_id")
    @classmethod
    def normalize_model_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Model ID must not be blank")
        if any(character in normalized for character in ("/", "?", "#", "\x00")):
            raise ValueError("Model ID contains an unsupported path character")
        return normalized

    @field_validator("parameters")
    @classmethod
    def validate_parameters(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_safe_parameters(value)


class AITaskProfilePut(BaseModel):
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    attempts_per_route: int = Field(default=1, ge=1, le=3)
    routes: list[AITaskRouteInput] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def reject_duplicate_routes(self) -> Self:
        route_keys = [(route.provider, route.model_id) for route in self.routes]
        if len(route_keys) != len(set(route_keys)):
            raise ValueError("Duplicate provider/model routes are not allowed")
        return self


class AITaskRouteResponse(BaseModel):
    provider: str
    model_id: str
    priority: int
    parameters: dict[str, object]


class AITaskProfileResponse(BaseModel):
    id: UUID
    task_type: AITaskType
    timeout_seconds: int
    attempts_per_route: int
    routes: list[AITaskRouteResponse]


def profile_response(
    profile: AITaskProfile,
    routes: list[AITaskRoute],
) -> AITaskProfileResponse:
    return AITaskProfileResponse(
        id=profile.id,
        task_type=AITaskType(profile.task_type),
        timeout_seconds=profile.timeout_seconds,
        attempts_per_route=profile.attempts_per_route,
        routes=[
            AITaskRouteResponse(
                provider=route.provider,
                model_id=route.model_id,
                priority=route.priority,
                parameters=route.parameters,
            )
            for route in routes
        ],
    )


async def load_profile(
    db: AsyncSession,
    tenant_id: UUID,
    task_type: AITaskType,
) -> AITaskProfile:
    profile = await db.scalar(
        select(AITaskProfile).where(
            AITaskProfile.tenant_id == tenant_id,
            AITaskProfile.task_type == task_type.value,
        )
    )
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI task profile not found")
    return profile


async def load_routes(
    db: AsyncSession,
    tenant_id: UUID,
    profile_id: UUID,
) -> list[AITaskRoute]:
    return list(
        (
            await db.scalars(
                select(AITaskRoute)
                .where(
                    AITaskRoute.tenant_id == tenant_id,
                    AITaskRoute.profile_id == profile_id,
                )
                .order_by(AITaskRoute.priority, AITaskRoute.id)
            )
        ).all()
    )


@router.get("", response_model=list[AITaskProfileResponse])
async def list_task_profiles(
    context: TenantContextDependency,
    db: DbSession,
) -> list[AITaskProfileResponse]:
    profiles = list(
        (
            await db.scalars(
                select(AITaskProfile)
                .where(AITaskProfile.tenant_id == context.tenant.id)
                .order_by(AITaskProfile.task_type, AITaskProfile.id)
            )
        ).all()
    )
    if not profiles:
        return []
    profile_ids = [profile.id for profile in profiles]
    routes = list(
        (
            await db.scalars(
                select(AITaskRoute)
                .where(
                    AITaskRoute.tenant_id == context.tenant.id,
                    AITaskRoute.profile_id.in_(profile_ids),
                )
                .order_by(AITaskRoute.profile_id, AITaskRoute.priority, AITaskRoute.id)
            )
        ).all()
    )
    grouped: dict[UUID, list[AITaskRoute]] = {profile_id: [] for profile_id in profile_ids}
    for route in routes:
        grouped[route.profile_id].append(route)
    return [profile_response(profile, grouped[profile.id]) for profile in profiles]


@router.get("/{task_type}", response_model=AITaskProfileResponse)
async def get_task_profile(
    task_type: AITaskType,
    context: TenantContextDependency,
    db: DbSession,
) -> AITaskProfileResponse:
    profile = await load_profile(db, context.tenant.id, task_type)
    routes = await load_routes(db, context.tenant.id, profile.id)
    return profile_response(profile, routes)


@router.put("/{task_type}", response_model=AITaskProfileResponse)
async def put_task_profile(
    task_type: AITaskType,
    payload: AITaskProfilePut,
    context: TenantContextDependency,
    db: DbSession,
) -> AITaskProfileResponse:
    require_tenant_role(context, WriteRole)
    profile = await db.scalar(
        select(AITaskProfile).where(
            AITaskProfile.tenant_id == context.tenant.id,
            AITaskProfile.task_type == task_type.value,
        )
    )
    if profile is None:
        profile = AITaskProfile(
            tenant_id=context.tenant.id,
            task_type=task_type.value,
            timeout_seconds=payload.timeout_seconds,
            attempts_per_route=payload.attempts_per_route,
        )
        db.add(profile)
        await db.flush()
    else:
        profile.timeout_seconds = payload.timeout_seconds
        profile.attempts_per_route = payload.attempts_per_route
        await db.execute(
            delete(AITaskRoute).where(
                AITaskRoute.tenant_id == context.tenant.id,
                AITaskRoute.profile_id == profile.id,
            )
        )

    for priority, route in enumerate(payload.routes):
        db.add(
            AITaskRoute(
                tenant_id=context.tenant.id,
                profile_id=profile.id,
                provider=route.provider,
                model_id=route.model_id,
                priority=priority,
                parameters=route.parameters,
            )
        )

    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="ai.task_profile.updated",
            target_type="ai_task_profile",
            target_id=profile.id,
            details={
                "task_type": task_type.value,
                "route_count": len(payload.routes),
            },
        )
    )
    await db.commit()
    profile = await load_profile(db, context.tenant.id, task_type)
    routes = await load_routes(db, context.tenant.id, profile.id)
    return profile_response(profile, routes)
