from datetime import UTC, datetime
from typing import Annotated, Literal, cast

import httpx2
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.ai_models import AITaskType
from customers_manager_hub.ai_profiles import AITaskProfilePut
from customers_manager_hub.ai_providers import GEMINI_BASE_URL, OPENAI_BASE_URL
from customers_manager_hub.config import Settings, get_settings
from customers_manager_hub.database import get_db_session
from customers_manager_hub.models import AuditEvent
from customers_manager_hub.platform_ai_models import (
    PlatformAIProvider,
    PlatformAIProviderCredential,
    PlatformAITaskProfile,
    PlatformAITaskRoute,
)
from customers_manager_hub.platform_ai_security import (
    decrypt_platform_ai_secret,
    encrypt_platform_ai_secret,
)
from customers_manager_hub.platform_auth import PlatformOwnerDependency

router = APIRouter(prefix="/api/v1/platform/ai", tags=["platform-ai"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
SettingsDependency = Annotated[Settings, Depends(get_settings)]
CredentialSource = Literal["database", "environment", "none"]

_GENERATION_TASKS = frozenset(
    {
        AITaskType.CUSTOMER_RESPONSE,
        AITaskType.INTENT_CLASSIFICATION,
        AITaskType.CONVERSATION_SUMMARY,
        AITaskType.CUSTOMER_MEMORY_EXTRACTION,
    }
)


class ProviderCredentialPut(BaseModel):
    api_key: str = Field(min_length=8, max_length=4096)


class ProviderStatusResponse(BaseModel):
    provider: PlatformAIProvider
    configured: bool
    source: CredentialSource
    last_tested_at: datetime | None
    last_test_succeeded: bool | None
    last_error_code: str | None


class ProviderConnectionTestResponse(BaseModel):
    provider: PlatformAIProvider
    success: bool
    model_count: int
    checked_at: datetime


class ProviderModelResponse(BaseModel):
    id: str
    display_name: str
    capabilities: list[str]


class PlatformTaskRouteResponse(BaseModel):
    provider: PlatformAIProvider
    model_id: str
    priority: int
    parameters: dict[str, object]


class PlatformTaskProfileResponse(BaseModel):
    task_type: AITaskType
    timeout_seconds: int
    attempts_per_route: int
    routes: list[PlatformTaskRouteResponse]


class ProviderDiscoveryError(RuntimeError):
    def __init__(self, code: str, *, response_status: int = status.HTTP_502_BAD_GATEWAY) -> None:
        super().__init__(code)
        self.code = code
        self.response_status = response_status


def _environment_secret(settings: Settings, provider: PlatformAIProvider) -> str | None:
    secret = (
        settings.openai_api_key
        if provider == PlatformAIProvider.OPENAI
        else settings.google_gemini_api_key
    )
    return secret.get_secret_value() if secret is not None else None


async def _credential_and_source(
    db: AsyncSession,
    settings: Settings,
    provider: PlatformAIProvider,
) -> tuple[PlatformAIProviderCredential | None, str | None, CredentialSource]:
    credential = await db.get(PlatformAIProviderCredential, provider.value)
    if credential is not None:
        return (
            credential,
            decrypt_platform_ai_secret(
                settings,
                provider,
                ciphertext=credential.ciphertext,
                nonce=credential.nonce,
                key_version=credential.key_version,
            ),
            "database",
        )
    fallback = _environment_secret(settings, provider)
    if fallback:
        return None, fallback, "environment"
    return None, None, "none"


def _provider_status(
    provider: PlatformAIProvider,
    credential: PlatformAIProviderCredential | None,
    source: CredentialSource,
) -> ProviderStatusResponse:
    return ProviderStatusResponse(
        provider=provider,
        configured=source != "none",
        source=source,
        last_tested_at=credential.last_tested_at if credential is not None else None,
        last_test_succeeded=credential.last_test_succeeded if credential is not None else None,
        last_error_code=credential.last_error_code if credential is not None else None,
    )


def _provider_headers(provider: PlatformAIProvider, api_key: str) -> dict[str, str]:
    if provider == PlatformAIProvider.OPENAI:
        return {"Authorization": f"Bearer {api_key}"}
    return {"x-goog-api-key": api_key}


def _provider_url(provider: PlatformAIProvider) -> str:
    if provider == PlatformAIProvider.OPENAI:
        return f"{OPENAI_BASE_URL}/models"
    return f"{GEMINI_BASE_URL}/models?pageSize=1000"


def _openai_capabilities(model_id: str) -> list[str]:
    normalized = model_id.casefold()
    if "embedding" in normalized:
        return ["embedding"]
    if "transcribe" in normalized or "whisper" in normalized:
        return ["transcription"]
    excluded = ("tts", "realtime", "audio", "image", "moderation")
    if any(fragment in normalized for fragment in excluded):
        return []
    return ["generation"]


def _gemini_capabilities(model_id: str, methods: list[str]) -> list[str]:
    normalized_methods = {method.casefold() for method in methods}
    capabilities: list[str] = []
    if "generatecontent" in normalized_methods:
        capabilities.append("generation")
        # Gemini transcription is implemented through multimodal generateContent,
        # not through a separate transcription method in the Models response.
        capabilities.append("transcription")
    if {"embedcontent", "batchembedcontents"} & normalized_methods:
        capabilities.append("embedding")
    if "transcribe" in model_id.casefold() and "transcription" not in capabilities:
        capabilities.append("transcription")
    return capabilities


def _parse_openai_models(payload: object) -> list[ProviderModelResponse]:
    if not isinstance(payload, dict):
        raise ProviderDiscoveryError("openai_invalid_response")
    mapping = cast(dict[str, object], payload)
    raw_models = mapping.get("data")
    if not isinstance(raw_models, list):
        raise ProviderDiscoveryError("openai_invalid_response")
    models: list[ProviderModelResponse] = []
    for item in cast(list[object], raw_models):
        if not isinstance(item, dict):
            continue
        model = cast(dict[str, object], item)
        raw_model_id = model.get("id")
        if not isinstance(raw_model_id, str):
            continue
        model_id = raw_model_id.strip()
        if model_id:
            models.append(
                ProviderModelResponse(
                    id=model_id,
                    display_name=model_id,
                    capabilities=_openai_capabilities(model_id),
                )
            )
    return sorted(models, key=lambda item: item.id.casefold())


def _parse_gemini_models(payload: object) -> list[ProviderModelResponse]:
    if not isinstance(payload, dict):
        raise ProviderDiscoveryError("gemini_invalid_response")
    mapping = cast(dict[str, object], payload)
    raw_models = mapping.get("models")
    if not isinstance(raw_models, list):
        raise ProviderDiscoveryError("gemini_invalid_response")
    models: list[ProviderModelResponse] = []
    for item in cast(list[object], raw_models):
        if not isinstance(item, dict):
            continue
        model = cast(dict[str, object], item)
        raw_name = model.get("name")
        if not isinstance(raw_name, str):
            continue
        model_id = raw_name.removeprefix("models/").strip()
        if not model_id:
            continue
        raw_methods = model.get("supportedGenerationMethods", [])
        methods = (
            [method for method in cast(list[object], raw_methods) if isinstance(method, str)]
            if isinstance(raw_methods, list)
            else []
        )
        display_name = model.get("displayName")
        models.append(
            ProviderModelResponse(
                id=model_id,
                display_name=display_name if isinstance(display_name, str) else model_id,
                capabilities=_gemini_capabilities(model_id, methods),
            )
        )
    return sorted(models, key=lambda item: item.id.casefold())


async def _discover_models(
    client: httpx2.AsyncClient,
    provider: PlatformAIProvider,
    api_key: str,
) -> list[ProviderModelResponse]:
    try:
        response = await client.get(
            _provider_url(provider),
            headers=_provider_headers(provider, api_key),
            timeout=10,
        )
    except httpx2.TimeoutException as exc:
        raise ProviderDiscoveryError(f"{provider.value}_timeout") from exc
    except httpx2.TransportError as exc:
        raise ProviderDiscoveryError(f"{provider.value}_transport_error") from exc
    if response.status_code >= 400:
        if response.status_code in {401, 403}:
            code = f"{provider.value}_authentication_failed"
            response_status = status.HTTP_422_UNPROCESSABLE_CONTENT
        elif response.status_code == 429:
            code = f"{provider.value}_rate_limited"
            response_status = status.HTTP_429_TOO_MANY_REQUESTS
        else:
            code = f"{provider.value}_http_{response.status_code}"
            response_status = status.HTTP_502_BAD_GATEWAY
        raise ProviderDiscoveryError(code, response_status=response_status)
    try:
        payload: object = response.json()
    except ValueError as exc:
        raise ProviderDiscoveryError(f"{provider.value}_invalid_response") from exc
    if provider == PlatformAIProvider.OPENAI:
        return _parse_openai_models(payload)
    return _parse_gemini_models(payload)


def _operation_for_task(task_type: AITaskType) -> str:
    if task_type in _GENERATION_TASKS:
        return "generation"
    if task_type == AITaskType.VOICE_TRANSCRIPTION:
        return "transcription"
    return "embedding"


def _external_http_client(request: Request) -> httpx2.AsyncClient:
    return cast(httpx2.AsyncClient, request.app.state.external_http_client)


@router.get("/providers", response_model=list[ProviderStatusResponse])
async def list_providers(
    owner: PlatformOwnerDependency,
    db: DbSession,
    settings: SettingsDependency,
) -> list[ProviderStatusResponse]:
    del owner
    response: list[ProviderStatusResponse] = []
    for provider in PlatformAIProvider:
        credential, _, source = await _credential_and_source(db, settings, provider)
        response.append(_provider_status(provider, credential, source))
    return response


@router.put("/providers/{provider}", response_model=ProviderStatusResponse)
async def put_provider_credential(
    provider: PlatformAIProvider,
    payload: ProviderCredentialPut,
    owner: PlatformOwnerDependency,
    db: DbSession,
    settings: SettingsDependency,
) -> ProviderStatusResponse:
    encrypted = encrypt_platform_ai_secret(settings, provider, payload.api_key)
    credential = await db.get(PlatformAIProviderCredential, provider.value)
    if credential is None:
        credential = PlatformAIProviderCredential(
            provider=provider.value,
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
            key_version=encrypted.key_version,
            updated_by_user_id=owner.id,
        )
        db.add(credential)
    else:
        credential.ciphertext = encrypted.ciphertext
        credential.nonce = encrypted.nonce
        credential.key_version = encrypted.key_version
        credential.updated_by_user_id = owner.id
        credential.last_tested_at = None
        credential.last_test_succeeded = None
        credential.last_error_code = None
    db.add(
        AuditEvent(
            tenant_id=None,
            actor_user_id=owner.id,
            action="platform.ai_provider.updated",
            target_type="platform_ai_provider",
            target_id=None,
            details={"provider": provider.value},
        )
    )
    await db.commit()
    await db.refresh(credential)
    return _provider_status(provider, credential, "database")


@router.delete("/providers/{provider}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider_credential(
    provider: PlatformAIProvider,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> Response:
    credential = await db.get(PlatformAIProviderCredential, provider.value)
    if credential is not None:
        await db.delete(credential)
        db.add(
            AuditEvent(
                tenant_id=None,
                actor_user_id=owner.id,
                action="platform.ai_provider.deleted",
                target_type="platform_ai_provider",
                target_id=None,
                details={"provider": provider.value},
            )
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/providers/{provider}/test",
    response_model=ProviderConnectionTestResponse,
)
async def test_provider_connection(
    provider: PlatformAIProvider,
    request: Request,
    owner: PlatformOwnerDependency,
    db: DbSession,
    settings: SettingsDependency,
) -> ProviderConnectionTestResponse:
    del owner
    credential, api_key, _ = await _credential_and_source(db, settings, provider)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider not configured")
    checked_at = datetime.now(UTC)
    try:
        models = await _discover_models(_external_http_client(request), provider, api_key)
    except ProviderDiscoveryError as exc:
        if credential is not None:
            credential.last_tested_at = checked_at
            credential.last_test_succeeded = False
            credential.last_error_code = exc.code
            await db.commit()
        raise HTTPException(status_code=exc.response_status, detail=exc.code) from None
    if credential is not None:
        credential.last_tested_at = checked_at
        credential.last_test_succeeded = True
        credential.last_error_code = None
        await db.commit()
    return ProviderConnectionTestResponse(
        provider=provider,
        success=True,
        model_count=len(models),
        checked_at=checked_at,
    )


@router.get("/providers/{provider}/models", response_model=list[ProviderModelResponse])
async def list_provider_models(
    provider: PlatformAIProvider,
    request: Request,
    owner: PlatformOwnerDependency,
    db: DbSession,
    settings: SettingsDependency,
    task_type: Annotated[AITaskType | None, Query()] = None,
) -> list[ProviderModelResponse]:
    del owner
    _, api_key, _ = await _credential_and_source(db, settings, provider)
    if api_key is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider not configured")
    try:
        models = await _discover_models(_external_http_client(request), provider, api_key)
    except ProviderDiscoveryError as exc:
        raise HTTPException(status_code=exc.response_status, detail=exc.code) from None
    if task_type is None:
        return models
    operation = _operation_for_task(task_type)
    return [model for model in models if operation in model.capabilities]


def _profile_response(
    profile: PlatformAITaskProfile,
    routes: list[PlatformAITaskRoute],
) -> PlatformTaskProfileResponse:
    return PlatformTaskProfileResponse(
        task_type=AITaskType(profile.task_type),
        timeout_seconds=profile.timeout_seconds,
        attempts_per_route=profile.attempts_per_route,
        routes=[
            PlatformTaskRouteResponse(
                provider=PlatformAIProvider(route.provider),
                model_id=route.model_id,
                priority=route.priority,
                parameters=route.parameters,
            )
            for route in routes
        ],
    )


@router.get("/task-profiles", response_model=list[PlatformTaskProfileResponse])
async def list_platform_task_profiles(
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> list[PlatformTaskProfileResponse]:
    del owner
    profiles = list(
        (
            await db.scalars(
                select(PlatformAITaskProfile).order_by(PlatformAITaskProfile.task_type)
            )
        ).all()
    )
    response: list[PlatformTaskProfileResponse] = []
    for profile in profiles:
        routes = list(
            (
                await db.scalars(
                    select(PlatformAITaskRoute)
                    .where(PlatformAITaskRoute.profile_id == profile.id)
                    .order_by(PlatformAITaskRoute.priority, PlatformAITaskRoute.id)
                )
            ).all()
        )
        response.append(_profile_response(profile, routes))
    return response


@router.put(
    "/task-profiles/{task_type}",
    response_model=PlatformTaskProfileResponse,
)
async def put_platform_task_profile(
    task_type: AITaskType,
    payload: AITaskProfilePut,
    owner: PlatformOwnerDependency,
    db: DbSession,
) -> PlatformTaskProfileResponse:
    profile = await db.scalar(
        select(PlatformAITaskProfile).where(PlatformAITaskProfile.task_type == task_type.value)
    )
    if profile is None:
        profile = PlatformAITaskProfile(
            task_type=task_type.value,
            timeout_seconds=payload.timeout_seconds,
            attempts_per_route=payload.attempts_per_route,
            updated_by_user_id=owner.id,
        )
        db.add(profile)
        await db.flush()
    else:
        profile.timeout_seconds = payload.timeout_seconds
        profile.attempts_per_route = payload.attempts_per_route
        profile.updated_by_user_id = owner.id
        await db.execute(
            delete(PlatformAITaskRoute).where(PlatformAITaskRoute.profile_id == profile.id)
        )
    for priority, route in enumerate(payload.routes):
        db.add(
            PlatformAITaskRoute(
                profile_id=profile.id,
                provider=route.provider,
                model_id=route.model_id,
                priority=priority,
                parameters=route.parameters,
            )
        )
    db.add(
        AuditEvent(
            tenant_id=None,
            actor_user_id=owner.id,
            action="platform.ai_task_profile.updated",
            target_type="platform_ai_task_profile",
            target_id=profile.id,
            details={"task_type": task_type.value, "route_count": len(payload.routes)},
        )
    )
    await db.commit()
    await db.refresh(profile)
    routes = list(
        (
            await db.scalars(
                select(PlatformAITaskRoute)
                .where(PlatformAITaskRoute.profile_id == profile.id)
                .order_by(PlatformAITaskRoute.priority, PlatformAITaskRoute.id)
            )
        ).all()
    )
    return _profile_response(profile, routes)
