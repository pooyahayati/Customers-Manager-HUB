import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Protocol, TypeVar
from uuid import UUID

from sqlalchemy import select

from customers_manager_hub.ai_models import (
    AIExecutionStatus,
    AIExecutionTrace,
    AITaskProfile,
    AITaskRoute,
    AITaskType,
)
from customers_manager_hub.database import AsyncSessionFactory


class AIOperation(StrEnum):
    GENERATION = "generation"
    EMBEDDING = "embedding"
    TRANSCRIPTION = "transcription"


TASK_OPERATION: dict[AITaskType, AIOperation] = {
    AITaskType.CUSTOMER_RESPONSE: AIOperation.GENERATION,
    AITaskType.VOICE_TRANSCRIPTION: AIOperation.TRANSCRIPTION,
    AITaskType.INTENT_CLASSIFICATION: AIOperation.GENERATION,
    AITaskType.CONVERSATION_SUMMARY: AIOperation.GENERATION,
    AITaskType.CUSTOMER_MEMORY_EXTRACTION: AIOperation.GENERATION,
    AITaskType.EMBEDDING: AIOperation.EMBEDDING,
}


@dataclass(frozen=True, slots=True)
class AIUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    audio_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    input_text: str
    instructions: str | None = None
    json_schema: dict[str, object] | None = None
    schema_name: str = "result"


@dataclass(frozen=True, slots=True)
class GenerationResult:
    provider: str
    model_id: str
    text: str
    structured: dict[str, object] | None
    usage: AIUsage
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    inputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    provider: str
    model_id: str
    embeddings: tuple[tuple[float, ...], ...]
    usage: AIUsage
    provider_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio: bytes
    filename: str
    mime_type: str
    prompt: str | None = None


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    provider: str
    model_id: str
    text: str
    usage: AIUsage
    provider_request_id: str | None = None


class AIProviderResult(Protocol):
    usage: AIUsage
    provider_request_id: str | None


class AIProviderAdapter(Protocol):
    @property
    def key(self) -> str: ...

    def supports(self, operation: AIOperation) -> bool: ...

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult: ...

    async def embed(
        self,
        model_id: str,
        request: EmbeddingRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> EmbeddingResult: ...

    async def transcribe(
        self,
        model_id: str,
        request: TranscriptionRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> TranscriptionResult: ...


class AIProviderError(Exception):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class AIRoutingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class AIProviderRegistry:
    def __init__(self, adapters: Iterable[AIProviderAdapter] = ()) -> None:
        self._adapters: dict[str, AIProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: AIProviderAdapter) -> None:
        key = adapter.key.strip().casefold()
        if not key:
            raise ValueError("AI provider key must not be blank")
        if key in self._adapters:
            raise ValueError(f"AI provider is already registered: {key}")
        self._adapters[key] = adapter

    def get(self, key: str) -> AIProviderAdapter | None:
        return self._adapters.get(key.casefold())

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._adapters))


@dataclass(frozen=True, slots=True)
class ResolvedRoute:
    provider: str
    model_id: str
    priority: int
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class ResolvedTaskProfile:
    id: UUID
    task_type: AITaskType
    timeout_seconds: int
    attempts_per_route: int
    routes: tuple[ResolvedRoute, ...]


ResultT = TypeVar("ResultT", bound=AIProviderResult)
ProviderCall = Callable[[AIProviderAdapter, ResolvedRoute], Awaitable[ResultT]]


class AIGateway:
    def __init__(
        self,
        registry: AIProviderRegistry,
        session_factory: AsyncSessionFactory,
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory

    async def generate(
        self,
        tenant_id: UUID,
        task_type: AITaskType,
        request: GenerationRequest,
    ) -> GenerationResult:
        self._require_operation(task_type, AIOperation.GENERATION)
        return await self._execute(
            tenant_id,
            task_type,
            AIOperation.GENERATION,
            lambda adapter, route: adapter.generate(
                route.model_id,
                request,
                route.parameters,
                self._profile_timeout_placeholder,
            ),
        )

    async def embed(self, tenant_id: UUID, request: EmbeddingRequest) -> EmbeddingResult:
        if not request.inputs or any(not item.strip() for item in request.inputs):
            raise ValueError("Embedding inputs must contain non-blank text")
        return await self._execute(
            tenant_id,
            AITaskType.EMBEDDING,
            AIOperation.EMBEDDING,
            lambda adapter, route: adapter.embed(
                route.model_id,
                request,
                route.parameters,
                self._profile_timeout_placeholder,
            ),
        )

    async def transcribe(
        self,
        tenant_id: UUID,
        request: TranscriptionRequest,
    ) -> TranscriptionResult:
        if not request.audio:
            raise ValueError("Transcription audio must not be empty")
        return await self._execute(
            tenant_id,
            AITaskType.VOICE_TRANSCRIPTION,
            AIOperation.TRANSCRIPTION,
            lambda adapter, route: adapter.transcribe(
                route.model_id,
                request,
                route.parameters,
                self._profile_timeout_placeholder,
            ),
        )

    @property
    def _profile_timeout_placeholder(self) -> int:
        raise RuntimeError("Profile timeout must be supplied by the routing loop")

    @staticmethod
    def _require_operation(task_type: AITaskType, operation: AIOperation) -> None:
        if TASK_OPERATION[task_type] != operation:
            raise ValueError(f"Task {task_type.value} does not use {operation.value}")

    async def _load_profile(
        self,
        tenant_id: UUID,
        task_type: AITaskType,
    ) -> ResolvedTaskProfile:
        async with self._session_factory() as db:
            profile = await db.scalar(
                select(AITaskProfile).where(
                    AITaskProfile.tenant_id == tenant_id,
                    AITaskProfile.task_type == task_type.value,
                )
            )
            if profile is None:
                raise AIRoutingError("task_profile_not_configured")
            routes = list(
                (
                    await db.scalars(
                        select(AITaskRoute)
                        .where(
                            AITaskRoute.tenant_id == tenant_id,
                            AITaskRoute.profile_id == profile.id,
                        )
                        .order_by(AITaskRoute.priority, AITaskRoute.id)
                    )
                ).all()
            )

        if not routes:
            raise AIRoutingError("task_profile_has_no_routes")
        return ResolvedTaskProfile(
            id=profile.id,
            task_type=AITaskType(profile.task_type),
            timeout_seconds=profile.timeout_seconds,
            attempts_per_route=profile.attempts_per_route,
            routes=tuple(
                ResolvedRoute(
                    provider=route.provider,
                    model_id=route.model_id,
                    priority=route.priority,
                    parameters=dict(route.parameters),
                )
                for route in routes
            ),
        )

    async def _execute(
        self,
        tenant_id: UUID,
        task_type: AITaskType,
        operation: AIOperation,
        call: ProviderCall[ResultT],
    ) -> ResultT:
        profile = await self._load_profile(tenant_id, task_type)
        last_error_code = "all_routes_failed"

        for route in profile.routes:
            adapter = self._registry.get(route.provider)
            if adapter is None:
                last_error_code = "provider_unavailable"
                await self._record_trace(
                    tenant_id=tenant_id,
                    profile=profile,
                    route=route,
                    attempt_number=1,
                    status=AIExecutionStatus.FAILED,
                    latency_ms=0,
                    usage=AIUsage(),
                    provider_request_id=None,
                    error_code=last_error_code,
                )
                continue
            if not adapter.supports(operation):
                last_error_code = "provider_capability_unavailable"
                await self._record_trace(
                    tenant_id=tenant_id,
                    profile=profile,
                    route=route,
                    attempt_number=1,
                    status=AIExecutionStatus.FAILED,
                    latency_ms=0,
                    usage=AIUsage(),
                    provider_request_id=None,
                    error_code=last_error_code,
                )
                continue

            for attempt_number in range(1, profile.attempts_per_route + 1):
                started = perf_counter()
                try:
                    result = await self._invoke_with_timeout(
                        call,
                        adapter,
                        route,
                        profile.timeout_seconds,
                    )
                except AIProviderError as exc:
                    latency_ms = max(0, round((perf_counter() - started) * 1000))
                    last_error_code = exc.code
                    await self._record_trace(
                        tenant_id=tenant_id,
                        profile=profile,
                        route=route,
                        attempt_number=attempt_number,
                        status=AIExecutionStatus.FAILED,
                        latency_ms=latency_ms,
                        usage=AIUsage(),
                        provider_request_id=None,
                        error_code=exc.code,
                    )
                    if exc.retryable and attempt_number < profile.attempts_per_route:
                        await asyncio.sleep(min(0.25 * (2 ** (attempt_number - 1)), 1.0))
                        continue
                    break

                latency_ms = max(0, round((perf_counter() - started) * 1000))
                await self._record_trace(
                    tenant_id=tenant_id,
                    profile=profile,
                    route=route,
                    attempt_number=attempt_number,
                    status=AIExecutionStatus.SUCCEEDED,
                    latency_ms=latency_ms,
                    usage=result.usage,
                    provider_request_id=result.provider_request_id,
                    error_code=None,
                )
                return result

        raise AIRoutingError(last_error_code)

    @staticmethod
    async def _invoke_with_timeout(
        call: ProviderCall[ResultT],
        adapter: AIProviderAdapter,
        route: ResolvedRoute,
        timeout_seconds: int,
    ) -> ResultT:
        try:
            async with asyncio.timeout(timeout_seconds):
                return await call(adapter, route)
        except TimeoutError as exc:
            raise AIProviderError("provider_timeout", retryable=True) from exc

    async def _record_trace(
        self,
        *,
        tenant_id: UUID,
        profile: ResolvedTaskProfile,
        route: ResolvedRoute,
        attempt_number: int,
        status: AIExecutionStatus,
        latency_ms: int,
        usage: AIUsage,
        provider_request_id: str | None,
        error_code: str | None,
    ) -> None:
        async with self._session_factory() as db:
            db.add(
                AIExecutionTrace(
                    tenant_id=tenant_id,
                    task_profile_id=profile.id,
                    task_type=profile.task_type.value,
                    provider=route.provider,
                    model_id=route.model_id,
                    route_priority=route.priority,
                    attempt_number=attempt_number,
                    status=status.value,
                    provider_request_id=provider_request_id,
                    latency_ms=latency_ms,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    total_tokens=usage.total_tokens,
                    audio_seconds=usage.audio_seconds,
                    error_code=error_code,
                )
            )
            await db.commit()


class MockAIProviderAdapter:
    def __init__(
        self,
        *,
        key: str = "mock",
        failures_before_success: int = 0,
        generation_text: str = "mock response",
        structured_payload: dict[str, object] | None = None,
        transcription_text: str = "mock transcript",
    ) -> None:
        self._key = key
        self._remaining_failures = failures_before_success
        self._generation_text = generation_text
        self._structured_payload = structured_payload
        self._transcription_text = transcription_text
        self.calls = 0

    @property
    def key(self) -> str:
        return self._key

    def supports(self, operation: AIOperation) -> bool:
        return operation in {
            AIOperation.GENERATION,
            AIOperation.EMBEDDING,
            AIOperation.TRANSCRIPTION,
        }

    def _begin_call(self) -> str:
        self.calls += 1
        request_id = f"mock-{self._key}-{self.calls}"
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise AIProviderError("mock_retryable_failure", retryable=True)
        return request_id

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        del parameters, timeout_seconds
        request_id = self._begin_call()
        structured = None
        text = self._generation_text
        if request.json_schema is not None:
            structured = self._structured_payload or {"value": "mock"}
            text = json.dumps(structured, separators=(",", ":"))
        return GenerationResult(
            provider=self.key,
            model_id=model_id,
            text=text,
            structured=structured,
            usage=AIUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            provider_request_id=request_id,
        )

    async def embed(
        self,
        model_id: str,
        request: EmbeddingRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> EmbeddingResult:
        del parameters, timeout_seconds
        request_id = self._begin_call()
        embeddings = tuple((float(len(text)), 1.0) for text in request.inputs)
        return EmbeddingResult(
            provider=self.key,
            model_id=model_id,
            embeddings=embeddings,
            usage=AIUsage(input_tokens=len(request.inputs), total_tokens=len(request.inputs)),
            provider_request_id=request_id,
        )

    async def transcribe(
        self,
        model_id: str,
        request: TranscriptionRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> TranscriptionResult:
        del request, parameters, timeout_seconds
        request_id = self._begin_call()
        return TranscriptionResult(
            provider=self.key,
            model_id=model_id,
            text=self._transcription_text,
            usage=AIUsage(),
            provider_request_id=request_id,
        )
