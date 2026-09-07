import hashlib
from contextlib import suppress
from pathlib import PurePosixPath
from typing import Annotated, Self, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from redis.exceptions import RedisError
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from customers_manager_hub.agent_models import Agent
from customers_manager_hub.database import get_db_session
from customers_manager_hub.knowledge_models import (
    AgentKnowledgePermission,
    KnowledgeBase,
    KnowledgeRetrievalTrace,
    KnowledgeSource,
    KnowledgeSourceStatus,
)
from customers_manager_hub.knowledge_queue import KnowledgeJobQueue
from customers_manager_hub.knowledge_storage import (
    MAX_KNOWLEDGE_OBJECT_BYTES,
    PDF_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    KnowledgeStorageError,
    ObjectStorage,
    knowledge_object_key,
)
from customers_manager_hub.models import AuditEvent, TenantRole
from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role

router = APIRouter(prefix="/api/v1/tenants/{tenant_id}", tags=["knowledge"])
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
ConfigWriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
MAX_KNOWLEDGE_UPLOAD_BYTES = MAX_KNOWLEDGE_OBJECT_BYTES
_READ_CHUNK_BYTES = 1024 * 1024


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Knowledge base name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Knowledge base name must not be blank")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def validate_patch(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one knowledge base setting must be supplied")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name must not be null")
        if "is_active" in self.model_fields_set and self.is_active is None:
            raise ValueError("is_active must not be null")
        return self


class KnowledgeBaseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_active: bool


class KnowledgeSourceResponse(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    filename: str
    media_type: str
    sha256: str
    size_bytes: int
    status: KnowledgeSourceStatus
    error_code: str | None
    chunk_count: int
    embedding_provider: str | None
    embedding_model_id: str | None
    embedding_dimension: int | None


class AgentKnowledgePermissionResponse(BaseModel):
    id: UUID
    agent_id: UUID
    knowledge_base_id: UUID


class RetrievalTraceResponse(BaseModel):
    id: UUID
    agent_id: UUID
    agent_run_id: UUID | None
    query_sha256: str
    embedding_provider: str
    embedding_model_id: str
    embedding_dimension: int
    knowledge_base_ids: list[str]
    selected_chunks: list[dict[str, object]]
    latency_ms: int


def get_knowledge_queue(request: Request) -> KnowledgeJobQueue:
    return cast(KnowledgeJobQueue, request.app.state.knowledge_queue)


def get_knowledge_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.knowledge_storage)


async def _load_base(db: AsyncSession, tenant_id: UUID, base_id: UUID) -> KnowledgeBase:
    base = await db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == base_id,
            KnowledgeBase.tenant_id == tenant_id,
        )
    )
    if base is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found"
        )
    return base


async def _load_source(db: AsyncSession, tenant_id: UUID, source_id: UUID) -> KnowledgeSource:
    source = await db.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.id == source_id,
            KnowledgeSource.tenant_id == tenant_id,
        )
    )
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge source not found"
        )
    return source


async def _load_agent(db: AsyncSession, tenant_id: UUID, agent_id: UUID) -> Agent:
    agent = await db.scalar(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return agent


def _base_response(base: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=base.id,
        name=base.name,
        description=base.description,
        is_active=base.is_active,
    )


def _source_response(source: KnowledgeSource) -> KnowledgeSourceResponse:
    return KnowledgeSourceResponse(
        id=source.id,
        knowledge_base_id=source.knowledge_base_id,
        filename=source.filename,
        media_type=source.media_type,
        sha256=source.sha256,
        size_bytes=source.size_bytes,
        status=KnowledgeSourceStatus(source.status),
        error_code=source.error_code,
        chunk_count=source.chunk_count,
        embedding_provider=source.embedding_provider,
        embedding_model_id=source.embedding_model_id,
        embedding_dimension=source.embedding_dimension,
    )


def _permission_response(permission: AgentKnowledgePermission) -> AgentKnowledgePermissionResponse:
    return AgentKnowledgePermissionResponse(
        id=permission.id,
        agent_id=permission.agent_id,
        knowledge_base_id=permission.knowledge_base_id,
    )


def _trace_response(trace: KnowledgeRetrievalTrace) -> RetrievalTraceResponse:
    return RetrievalTraceResponse(
        id=trace.id,
        agent_id=trace.agent_id,
        agent_run_id=trace.agent_run_id,
        query_sha256=trace.query_sha256,
        embedding_provider=trace.embedding_provider,
        embedding_model_id=trace.embedding_model_id,
        embedding_dimension=trace.embedding_dimension,
        knowledge_base_ids=list(trace.knowledge_base_ids),
        selected_chunks=[dict(item) for item in trace.selected_chunks],
        latency_ms=trace.latency_ms,
    )


def _normalized_upload_metadata(file: UploadFile) -> tuple[str, str]:
    raw_filename = (file.filename or "").replace("\\", "/")
    filename = PurePosixPath(raw_filename).name.strip()
    filename = "".join(
        character for character in filename if character >= " " and character != "\x7f"
    )
    if not filename or len(filename) > 255:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid filename"
        )
    extension = PurePosixPath(filename).suffix.casefold()
    if extension == ".pdf":
        expected_media_type = PDF_MEDIA_TYPE
    elif extension == ".xlsx":
        expected_media_type = XLSX_MEDIA_TYPE
    else:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and XLSX knowledge sources are supported",
        )
    supplied_media_type = (file.content_type or "").split(";", 1)[0].strip().casefold()
    if supplied_media_type not in {expected_media_type, "application/octet-stream", ""}:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File content type does not match extension",
        )
    return filename, expected_media_type


async def _read_bounded_upload(file: UploadFile) -> bytes:
    body = bytearray()
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        if len(body) + len(chunk) > MAX_KNOWLEDGE_UPLOAD_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        body.extend(chunk)
    if not body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Empty file")
    return bytes(body)


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    context: TenantContextDependency,
    db: DbSession,
) -> KnowledgeBaseResponse:
    require_tenant_role(context, ConfigWriteRole)
    base = KnowledgeBase(
        tenant_id=context.tenant.id,
        name=payload.name,
        description=payload.description,
        is_active=True,
    )
    db.add(base)
    try:
        await db.flush()
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="knowledge.base.created",
                target_type="knowledge_base",
                target_id=base.id,
                details={"name": base.name},
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Knowledge base exists"
        ) from exc
    await db.refresh(base)
    return _base_response(base)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    context: TenantContextDependency,
    db: DbSession,
) -> list[KnowledgeBaseResponse]:
    bases = list(
        (
            await db.scalars(
                select(KnowledgeBase)
                .where(KnowledgeBase.tenant_id == context.tenant.id)
                .order_by(KnowledgeBase.name, KnowledgeBase.id)
            )
        ).all()
    )
    return [_base_response(base) for base in bases]


@router.get("/knowledge-bases/{base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    base_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> KnowledgeBaseResponse:
    return _base_response(await _load_base(db, context.tenant.id, base_id))


@router.patch("/knowledge-bases/{base_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    base_id: UUID,
    payload: KnowledgeBaseUpdate,
    context: TenantContextDependency,
    db: DbSession,
) -> KnowledgeBaseResponse:
    require_tenant_role(context, ConfigWriteRole)
    base = await _load_base(db, context.tenant.id, base_id)
    changed: list[str] = []
    for field_name in payload.model_fields_set:
        value = getattr(payload, field_name)
        if getattr(base, field_name) != value:
            setattr(base, field_name, value)
            changed.append(field_name)
    if changed:
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="knowledge.base.updated",
                target_type="knowledge_base",
                target_id=base.id,
                details={"changed_fields": sorted(changed)},
            )
        )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Knowledge base exists"
            ) from exc
        await db.refresh(base)
    return _base_response(base)


@router.post(
    "/knowledge-bases/{base_id}/sources",
    response_model=KnowledgeSourceResponse,
    status_code=202,
)
async def upload_knowledge_source(
    base_id: UUID,
    file: Annotated[UploadFile, File(...)],
    request: Request,
    context: TenantContextDependency,
    db: DbSession,
    queue: Annotated[KnowledgeJobQueue, Depends(get_knowledge_queue)],
    storage: Annotated[ObjectStorage, Depends(get_knowledge_storage)],
) -> KnowledgeSourceResponse:
    del request
    require_tenant_role(context, ConfigWriteRole)
    await _load_base(db, context.tenant.id, base_id)
    filename, media_type = _normalized_upload_metadata(file)
    data = await _read_bounded_upload(file)
    digest = hashlib.sha256(data).hexdigest()
    duplicate = await db.scalar(
        select(KnowledgeSource).where(
            KnowledgeSource.tenant_id == context.tenant.id,
            KnowledgeSource.knowledge_base_id == base_id,
            KnowledgeSource.sha256 == digest,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Source content already exists"
        )

    source_id = uuid4()
    object_key = knowledge_object_key(context.tenant.id, base_id, source_id, media_type)
    try:
        await storage.put_bytes(object_key, data, media_type)
    except KnowledgeStorageError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc

    source = KnowledgeSource(
        id=source_id,
        tenant_id=context.tenant.id,
        knowledge_base_id=base_id,
        filename=filename,
        media_type=media_type,
        object_key=object_key,
        sha256=digest,
        size_bytes=len(data),
        status=KnowledgeSourceStatus.QUEUED.value,
        error_code=None,
        chunk_count=0,
        created_by_user_id=context.current.user.id,
    )
    db.add(source)
    try:
        await db.flush()
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="knowledge.source.uploaded",
                target_type="knowledge_source",
                target_id=source.id,
                details={
                    "knowledge_base_id": str(base_id),
                    "filename": filename,
                    "media_type": media_type,
                    "sha256": digest,
                    "size_bytes": len(data),
                },
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        with suppress(KnowledgeStorageError):
            await storage.delete_object(object_key)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Source content already exists"
        ) from exc

    try:
        await queue.enqueue(source.id)
    except RedisError as exc:
        await db.execute(
            delete(AuditEvent).where(
                AuditEvent.tenant_id == context.tenant.id,
                AuditEvent.action == "knowledge.source.uploaded",
                AuditEvent.target_type == "knowledge_source",
                AuditEvent.target_id == source.id,
            )
        )
        await db.delete(source)
        await db.commit()
        with suppress(KnowledgeStorageError):
            await storage.delete_object(object_key)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    await db.refresh(source)
    return _source_response(source)


@router.get(
    "/knowledge-bases/{base_id}/sources",
    response_model=list[KnowledgeSourceResponse],
)
async def list_knowledge_sources(
    base_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> list[KnowledgeSourceResponse]:
    await _load_base(db, context.tenant.id, base_id)
    sources = list(
        (
            await db.scalars(
                select(KnowledgeSource)
                .where(
                    KnowledgeSource.tenant_id == context.tenant.id,
                    KnowledgeSource.knowledge_base_id == base_id,
                )
                .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id.desc())
            )
        ).all()
    )
    return [_source_response(source) for source in sources]


@router.get("/knowledge-sources/{source_id}", response_model=KnowledgeSourceResponse)
async def get_knowledge_source(
    source_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> KnowledgeSourceResponse:
    return _source_response(await _load_source(db, context.tenant.id, source_id))


@router.post("/knowledge-sources/{source_id}/reindex", response_model=KnowledgeSourceResponse)
async def reindex_knowledge_source(
    source_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
    queue: Annotated[KnowledgeJobQueue, Depends(get_knowledge_queue)],
) -> KnowledgeSourceResponse:
    require_tenant_role(context, ConfigWriteRole)
    source = await db.scalar(
        select(KnowledgeSource)
        .where(
            KnowledgeSource.id == source_id,
            KnowledgeSource.tenant_id == context.tenant.id,
        )
        .with_for_update()
    )
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge source not found"
        )
    if source.status == KnowledgeSourceStatus.PROCESSING.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Source is processing")
    source.status = KnowledgeSourceStatus.QUEUED.value
    source.error_code = None
    db.add(
        AuditEvent(
            tenant_id=context.tenant.id,
            actor_user_id=context.current.user.id,
            action="knowledge.source.reindexed",
            target_type="knowledge_source",
            target_id=source.id,
            details={"knowledge_base_id": str(source.knowledge_base_id)},
        )
    )
    await db.commit()
    try:
        await queue.enqueue(source.id)
    except RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
    await db.refresh(source)
    return _source_response(source)


@router.get(
    "/agents/{agent_id}/knowledge-bases",
    response_model=list[AgentKnowledgePermissionResponse],
)
async def list_agent_knowledge_permissions(
    agent_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> list[AgentKnowledgePermissionResponse]:
    await _load_agent(db, context.tenant.id, agent_id)
    permissions = list(
        (
            await db.scalars(
                select(AgentKnowledgePermission)
                .where(
                    AgentKnowledgePermission.tenant_id == context.tenant.id,
                    AgentKnowledgePermission.agent_id == agent_id,
                )
                .order_by(AgentKnowledgePermission.created_at, AgentKnowledgePermission.id)
            )
        ).all()
    )
    return [_permission_response(permission) for permission in permissions]


@router.put(
    "/agents/{agent_id}/knowledge-bases/{base_id}",
    response_model=AgentKnowledgePermissionResponse,
)
async def grant_agent_knowledge_permission(
    agent_id: UUID,
    base_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> AgentKnowledgePermissionResponse:
    require_tenant_role(context, ConfigWriteRole)
    await _load_agent(db, context.tenant.id, agent_id)
    await _load_base(db, context.tenant.id, base_id)
    permission = await db.scalar(
        select(AgentKnowledgePermission).where(
            AgentKnowledgePermission.tenant_id == context.tenant.id,
            AgentKnowledgePermission.agent_id == agent_id,
            AgentKnowledgePermission.knowledge_base_id == base_id,
        )
    )
    if permission is None:
        permission = AgentKnowledgePermission(
            tenant_id=context.tenant.id,
            agent_id=agent_id,
            knowledge_base_id=base_id,
        )
        db.add(permission)
        await db.flush()
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="knowledge.permission.granted",
                target_type="agent_knowledge_permission",
                target_id=permission.id,
                details={"agent_id": str(agent_id), "knowledge_base_id": str(base_id)},
            )
        )
        await db.commit()
        await db.refresh(permission)
    return _permission_response(permission)


@router.delete("/agents/{agent_id}/knowledge-bases/{base_id}", status_code=204)
async def revoke_agent_knowledge_permission(
    agent_id: UUID,
    base_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> Response:
    require_tenant_role(context, ConfigWriteRole)
    await _load_agent(db, context.tenant.id, agent_id)
    await _load_base(db, context.tenant.id, base_id)
    permission = await db.scalar(
        select(AgentKnowledgePermission).where(
            AgentKnowledgePermission.tenant_id == context.tenant.id,
            AgentKnowledgePermission.agent_id == agent_id,
            AgentKnowledgePermission.knowledge_base_id == base_id,
        )
    )
    if permission is not None:
        permission_id = permission.id
        await db.delete(permission)
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="knowledge.permission.revoked",
                target_type="agent_knowledge_permission",
                target_id=permission_id,
                details={"agent_id": str(agent_id), "knowledge_base_id": str(base_id)},
            )
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/knowledge-retrieval-traces", response_model=list[RetrievalTraceResponse])
async def list_retrieval_traces(
    context: TenantContextDependency,
    db: DbSession,
) -> list[RetrievalTraceResponse]:
    traces = list(
        (
            await db.scalars(
                select(KnowledgeRetrievalTrace)
                .where(KnowledgeRetrievalTrace.tenant_id == context.tenant.id)
                .order_by(
                    KnowledgeRetrievalTrace.created_at.desc(), KnowledgeRetrievalTrace.id.desc()
                )
                .limit(100)
            )
        ).all()
    )
    return [_trace_response(trace) for trace in traces]


@router.get("/knowledge-retrieval-traces/{trace_id}", response_model=RetrievalTraceResponse)
async def get_retrieval_trace(
    trace_id: UUID,
    context: TenantContextDependency,
    db: DbSession,
) -> RetrievalTraceResponse:
    trace = await db.scalar(
        select(KnowledgeRetrievalTrace).where(
            KnowledgeRetrievalTrace.id == trace_id,
            KnowledgeRetrievalTrace.tenant_id == context.tenant.id,
        )
    )
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Retrieval trace not found"
        )
    return _trace_response(trace)
