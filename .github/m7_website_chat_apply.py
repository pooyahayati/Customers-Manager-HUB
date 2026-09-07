from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dedent(content).lstrip())


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text()
    if old not in text:
        raise RuntimeError(f"expected patch anchor not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1))


# ---------------------------------------------------------------------------
# Specification and ADR
# ---------------------------------------------------------------------------
write(
    "specs/MILESTONE-7-WEBSITE-CHAT.md",
    r'''
    # Milestone 7 Specification — Website Chat Channel

    ## Status

    Implementation specification for Roadmap Milestone 7.

    ## Goal

    Add a public Website Chat transport that reuses the canonical Contact, Conversation,
    Message, Agent, Prompt, AI Gateway, worker, and outbound-message runtime already proven
    by Telegram. A tenant must be able to configure a Website channel with exact allowed
    origins, embed a lightweight browser widget, receive a customer text message, and obtain
    the Agent response through the same `customer_response` task profile without duplicating
    AI business logic.

    ## In Scope

    ### Website channel configuration

    - Tenant-owned Website `ChannelAccount` using the existing channel domain.
    - Owner/Admin configuration API.
    - Exact allowed-origin list per Website channel account.
    - Text receive and outbound-text capability declaration.
    - Existing Agent-to-channel assignment is reused unchanged.
    - Configuration mutation is audited without session tokens or customer message content.

    ### Public browser session

    - Public session creation is addressed by opaque channel-account ID, never by tenant ID.
    - Every public request validates the browser `Origin` against the configured exact-origin list.
    - Session creation returns a cryptographically random opaque token once.
    - Only a SHA-256 digest of the session token is persisted.
    - Sessions expire after a bounded lifetime and are scoped to one tenant/channel account.
    - A session owns an anonymous stable visitor UUID used by canonical identity resolution.
    - The contract intentionally leaves a future extension point for a server-verified known-user
      identity assertion; browsers may not self-assert trusted CRM/customer identity in M7.

    ### Inbound transport

    - Public Website messages are text-only in M7.
    - Browser supplies a UUID `client_message_id` for retry-safe idempotency.
    - Website payload is normalized into `CanonicalInboundMessage` before persistence.
    - The existing `persist_inbound_event()` path resolves Contact/ExternalIdentity/Conversation,
      persists canonical Message, and enqueues the Redis Streams job.
    - Web requests never execute AI synchronously.

    ### Outbound transport

    - Website outbound delivery is canonical-message persistence, not an external provider API call.
    - Agent Runtime continues to call generic `dispatch_text()`.
    - For Website channels, `dispatch_text()` persists the outbound Message with the existing
      PostgreSQL advisory idempotency boundary and a synthetic Website external message ID.
    - Browser clients retrieve canonical text messages through an authenticated polling endpoint.
    - Initial polling avoids adding WebSocket/SSE connection infrastructure before it is required.

    ### Webchat artifact

    - `apps/webchat` is a separate lightweight public-client artifact as required by ADR-007.
    - It has no dependency on `apps/web`, Admin authentication, React, or Next.js.
    - The initial widget is a dependency-free browser script using Shadow DOM.
    - It accepts `data-api-base` and `data-channel-account-id` runtime configuration.
    - It stores only Website session ID/token in browser local storage when available.
    - It renders message content with `textContent`, not untrusted HTML.

    ## Data Model

    Extend `channel_accounts.channel_type` to support `website` in addition to `telegram`.

    New tables:

    - `website_channel_origins`
      - tenant ID
      - channel-account ID
      - normalized exact origin
      - created timestamp
      - unique `(channel_account_id, origin)`
    - `website_chat_sessions`
      - tenant ID
      - channel-account ID
      - anonymous visitor UUID
      - SHA-256 session-token digest
      - expiry timestamp
      - created/updated timestamps

    No Website-specific Conversation, Message, Agent, Prompt, or AI tables are introduced.

    ## API Surface

    Tenant Admin API:

    - `POST /api/v1/tenants/{tenant_id}/channels/website`
    - `GET /api/v1/tenants/{tenant_id}/channels/website`
    - `GET /api/v1/tenants/{tenant_id}/channels/website/{channel_account_id}`
    - `PUT /api/v1/tenants/{tenant_id}/channels/website/{channel_account_id}/origins`

    Public Website API:

    - `OPTIONS /api/v1/public/website/{channel_account_id}/{path...}`
    - `POST /api/v1/public/website/{channel_account_id}/sessions`
    - `POST /api/v1/public/website/{channel_account_id}/sessions/{session_id}/messages`
    - `GET /api/v1/public/website/{channel_account_id}/sessions/{session_id}/messages`

    Public message/session calls never accept a tenant ID as authorization input.

    ## Origin Policy

    Allowed origins are normalized and stored as exact origins only:

    - scheme must be `https`, except loopback/localhost development origins may use `http`;
    - host is required;
    - explicit port is preserved;
    - userinfo, path (other than `/`), query, and fragment are rejected;
    - wildcard origins are not supported in M7;
    - `Origin: null` is rejected.

    Every public endpoint, including CORS preflight, resolves the channel account first and checks
    the request origin against its tenant-scoped origin rows.

    ## Session Security

    - Session tokens use at least 256 bits of cryptographic randomness.
    - Raw tokens are returned only at session creation and never persisted, logged, audited, placed
      in Redis jobs, or exposed by Admin APIs.
    - Token verification compares the stored digest and presented-token digest using constant-time
      comparison.
    - Sessions expire after 30 days in M7.
    - Public message reads/writes require the token and exact allowed origin.
    - Channel deactivation immediately blocks new and existing public Website requests.

    ## Canonical Identity and Conversation Mapping

    For Website sessions:

    - sender namespace: `website:visitor`
    - sender external ID: session visitor UUID
    - external thread ID: Website session UUID
    - external message ID: browser-generated `client_message_id`

    The generic channel runtime therefore creates/reuses canonical Contact, ExternalIdentity,
    Conversation, ConversationChannelBinding, Message, and ChannelInboundEvent records.

    ## Agent Runtime

    Website text events use the existing Agent assignment and durable AgentRun flow.

    The only Agent-runtime Website-specific behavior is a deterministic channel instruction:

    - plain text only;
    - maximum 4096 characters for the initial widget transport.

    Customer content remains model input, not a trusted instruction layer.

    ## Explicitly Out of Scope

    - WebSocket/SSE streaming.
    - Typing indicators.
    - Attachments/media in Website Chat.
    - Browser-side trusted known-customer assertions.
    - Human handoff/operator inbox (M11).
    - Per-tenant custom widget builds.
    - Rich cards/buttons/reactions.
    - Website voice.
    - RAG, tools, memory, or policy changes.
    - Global rate limiting/abuse controls beyond the exact-origin/session boundary; production-grade
      rate limiting remains part of later hardening and policy milestones.

    ## Acceptance Tests

    Automated coverage must include at least:

    1. Owner/Admin can configure Website channels/origins; lower roles cannot mutate them.
    2. Cross-tenant Website channel reads return non-leaking 404 behavior.
    3. Invalid/wildcard/non-HTTPS public origins are rejected except localhost development HTTP.
    4. Public session creation succeeds only from an allowed origin and returns CORS headers.
    5. Raw Website session token is not persisted or included in audit/Redis payloads.
    6. Wrong session token, expired session, disallowed origin, inactive channel, and mismatched
       channel/session IDs are denied.
    7. Retrying the same `client_message_id` does not duplicate canonical inbound Message/Event.
    8. Website inbound text enters the existing Redis channel job queue.
    9. Worker processing invokes the same mock `customer_response` task profile and Agent Runtime
       used by Telegram.
    10. Website Agent response persists as one canonical outbound AI Message through
        `dispatch_text()` and AgentRun reaches `succeeded`.
    11. Public message polling returns the canonical inbound/outbound text conversation without
        exposing internal metadata, prompt content, provider trace, tenant ID, or credentials.
    12. Reprocessing/retrying a succeeded Website inbound event does not invoke AI again.
    13. Telegram tests remain green after generalizing channel type support.
    14. Migration applies from `0005`, `alembic check` reports no drift, and Docker runtime remains
        operational.

    ## Exit Gate

    A tenant can configure a Website channel and allowed origin, assign an existing Agent, embed
    the lightweight Webchat client, open a public browser session, send text, and receive one
    AI-authored reply through the same canonical Conversation/Agent/AI Gateway/worker runtime used
    by Telegram, with origin isolation and retry-safe persistence enforced server-side.
    ''',
)

write(
    "docs/adr/ADR-013-website-chat-public-session-and-polling-transport.md",
    r'''
    # ADR-013 — Website Chat Public Session and Polling Transport

    ## Status

    Accepted

    ## Context

    Milestone 7 introduces the first browser-originated public channel. Unlike Telegram, there is no
    external provider webhook or bot credential that authenticates inbound traffic. A browser widget
    runs on a tenant-controlled website, needs a narrowly scoped public identity, and must not inherit
    Admin Console authentication or expose a tenant-wide secret.

    The first Website transport also needs outbound delivery. WebSocket or SSE infrastructure would
    add connection lifecycle, proxy, scaling, and retry concerns before the product requires realtime
    streaming semantics.

    ADR-007 already requires Customer Webchat to remain a separate lightweight frontend artifact and
    to use the shared backend conversation/channel contracts.

    ## Decision

    ### Public trust boundary

    A Website channel account owns an exact list of allowed browser origins. Public Website requests
    resolve the channel account server-side and require an exact `Origin` match. Wildcards are not
    accepted.

    A public session is authenticated by a cryptographically random opaque session token returned
    once to the browser. PostgreSQL stores only the SHA-256 digest. The session is scoped to one
    tenant/channel account, has a bounded expiration, and owns an anonymous visitor UUID.

    The browser is not allowed to self-assert a trusted customer/CRM identity. A future known-customer
    upgrade must use a server-verifiable assertion or other trusted integration boundary.

    ### Canonical processing

    Website inbound text is normalized to the existing `CanonicalInboundMessage` and uses the same
    channel persistence, Redis Streams worker, Agent assignment, Prompt, AI Gateway, AgentRun, and
    canonical Message flow as Telegram.

    No Website-specific AI runtime is introduced.

    ### Outbound delivery

    Website outbound text is considered delivered to the Website transport when it is durably stored
    as the canonical outbound Message under the existing dispatch idempotency lock. The browser reads
    messages through the public session endpoint.

    Milestone 7 uses bounded polling rather than WebSocket/SSE. This keeps the initial deployment
    compatible with ordinary HTTP reverse proxies and avoids introducing a connection broker or
    sticky-session assumptions. A future push transport may replace polling without changing Agent or
    Conversation semantics.

    ### Public client artifact

    The first client lives in `apps/webchat` and is dependency-free browser JavaScript. It is separate
    from the Next.js Admin Console and communicates only with the public Website transport API.

    ## Alternatives Considered

    ### Public tenant API key embedded in the widget

    Rejected. A long-lived tenant/channel secret embedded in public JavaScript is not a secret and
    would create a broad replay/abuse credential.

    ### Trust Origin without a per-session token

    Rejected. Origin validation limits browser origins but is not an end-user/session identity and is
    insufficient for protecting conversation reads and writes.

    ### Cookie-based cross-site session

    Rejected for the initial widget because third-party cookie restrictions vary by browser and embed
    topology. An explicit scoped token is more predictable for an embeddable client.

    ### WebSocket or SSE in Milestone 7

    Deferred. Both are viable future transports, but polling is sufficient to prove omnichannel core
    reuse without adding unnecessary connection infrastructure.

    ## Consequences

    Positive:

    - Website Chat reuses all canonical backend business logic.
    - Public credentials are session-scoped rather than tenant-wide.
    - Exact origins are explicit tenant configuration and enforcement data.
    - Deployment remains simple HTTP + PostgreSQL + Redis.
    - The public widget remains small and independent of the Admin Console.

    Trade-offs:

    - Polling has higher request overhead and response latency than a push connection.
    - Session tokens live in browser storage when persistence is enabled by the embedding site.
    - Exact origin configuration requires operators to register every supported scheme/host/port.
    - Production abuse/rate controls still need the later hardening/policy work.

    ## Follow-up Implications

    - M11 Human Handoff can surface human outbound messages through the same Website polling API.
    - A later SSE/WebSocket transport must preserve the same canonical Message source of truth.
    - Known-customer Website identity must use a trusted server-side assertion design; arbitrary
      browser identity fields must not become authorization evidence.
    - Production rate limiting should key on channel account, origin, session, and network signals
      without placing Redis-only state above PostgreSQL conversation truth.
    ''',
)

replace_once(
    "docs/adr/README.md",
    "- ADR-012 — Versioned Prompts and Durable Agent Runs\n",
    "- ADR-012 — Versioned Prompts and Durable Agent Runs\n"
    "- ADR-013 — Website Chat Public Session and Polling Transport\n",
)

# ---------------------------------------------------------------------------
# Channel data model
# ---------------------------------------------------------------------------
replace_once(
    "apps/api/src/customers_manager_hub/channel_models.py",
    'class ChannelType(StrEnum):\n    TELEGRAM = "telegram"\n',
    'class ChannelType(StrEnum):\n    TELEGRAM = "telegram"\n    WEBSITE = "website"\n',
)
replace_once(
    "apps/api/src/customers_manager_hub/channel_models.py",
    'CheckConstraint("channel_type IN (\'telegram\')", name="ck_channel_accounts_type"),',
    'CheckConstraint("channel_type IN (\'telegram\', \'website\')", name="ck_channel_accounts_type"),',
)
with (ROOT / "apps/api/src/customers_manager_hub/channel_models.py").open("a") as handle:
    handle.write(
        dedent(
            r'''


            class WebsiteChannelOrigin(Base):
                __tablename__ = "website_channel_origins"
                __table_args__ = (
                    ForeignKeyConstraint(
                        ["channel_account_id", "tenant_id"],
                        ["channel_accounts.id", "channel_accounts.tenant_id"],
                        ondelete="CASCADE",
                        name="fk_website_channel_origins_account_tenant",
                    ),
                    UniqueConstraint(
                        "channel_account_id",
                        "origin",
                        name="uq_website_channel_origins_account_origin",
                    ),
                    Index(
                        "ix_website_channel_origins_tenant_account",
                        "tenant_id",
                        "channel_account_id",
                    ),
                )

                id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
                tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
                channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
                origin: Mapped[str] = mapped_column(String(512), nullable=False)
                created_at: Mapped[datetime] = mapped_column(
                    DateTime(timezone=True), server_default=func.now(), nullable=False
                )


            class WebsiteChatSession(Base):
                __tablename__ = "website_chat_sessions"
                __table_args__ = (
                    ForeignKeyConstraint(
                        ["channel_account_id", "tenant_id"],
                        ["channel_accounts.id", "channel_accounts.tenant_id"],
                        ondelete="CASCADE",
                        name="fk_website_chat_sessions_account_tenant",
                    ),
                    UniqueConstraint(
                        "channel_account_id",
                        "visitor_id",
                        name="uq_website_chat_sessions_account_visitor",
                    ),
                    UniqueConstraint("token_hash", name="uq_website_chat_sessions_token_hash"),
                    Index(
                        "ix_website_chat_sessions_tenant_account",
                        "tenant_id",
                        "channel_account_id",
                        "created_at",
                    ),
                    Index("ix_website_chat_sessions_expires", "expires_at"),
                )

                id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
                tenant_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
                channel_account_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
                visitor_id: Mapped[UUID] = mapped_column(
                    PGUUID(as_uuid=True), nullable=False, default=uuid4
                )
                token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
                expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
                created_at: Mapped[datetime] = mapped_column(
                    DateTime(timezone=True), server_default=func.now(), nullable=False
                )
                updated_at: Mapped[datetime] = mapped_column(
                    DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
                )
            '''
        )
    )

# ---------------------------------------------------------------------------
# Website channel adapter and origin normalization
# ---------------------------------------------------------------------------
write(
    "apps/api/src/customers_manager_hub/website.py",
    r'''
    from urllib.parse import urlsplit

    from customers_manager_hub.channel_gateway import (
        ChannelAccountIdentity,
        ChannelMediaReference,
        ChannelProviderError,
        ChannelSendResult,
    )
    from customers_manager_hub.channel_models import ChannelCapability, ChannelType

    _LOCAL_HTTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


    def normalize_website_origin(value: str) -> str:
        candidate = value.strip()
        if not candidate or candidate == "null" or "*" in candidate:
            raise ValueError("Website origin must be an exact non-wildcard origin")
        parts = urlsplit(candidate)
        if parts.scheme not in {"http", "https"} or parts.hostname is None:
            raise ValueError("Website origin must use http or https and include a host")
        if parts.username is not None or parts.password is not None:
            raise ValueError("Website origin must not contain user information")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("Website origin must not contain a path, query, or fragment")
        host = parts.hostname.lower()
        if parts.scheme == "http" and host not in _LOCAL_HTTP_HOSTS:
            raise ValueError("Non-local Website origins must use https")
        try:
            port = parts.port
        except ValueError as exc:
            raise ValueError("Website origin contains an invalid port") from exc
        rendered_host = f"[{host}]" if ":" in host else host
        netloc = rendered_host if port is None else f"{rendered_host}:{port}"
        return f"{parts.scheme}://{netloc}"


    class WebsiteAdapter:
        channel_type = ChannelType.WEBSITE
        capabilities = frozenset({ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT})

        async def validate_account(self, access_secret: str) -> ChannelAccountIdentity:
            del access_secret
            raise ChannelProviderError("website_external_account_unsupported", retryable=False)

        async def register_webhook(
            self,
            access_secret: str,
            *,
            webhook_url: str,
            webhook_secret: str,
        ) -> None:
            del access_secret, webhook_url, webhook_secret
            raise ChannelProviderError("website_webhook_unsupported", retryable=False)

        async def resolve_media(
            self,
            access_secret: str,
            external_media_id: str,
        ) -> ChannelMediaReference:
            del access_secret, external_media_id
            raise ChannelProviderError("website_media_unsupported", retryable=False)

        async def send_text(
            self,
            access_secret: str,
            *,
            external_thread_id: str,
            text: str,
        ) -> ChannelSendResult:
            del access_secret, external_thread_id, text
            raise ChannelProviderError("website_external_send_unsupported", retryable=False)
    ''',
)

# ---------------------------------------------------------------------------
# Website admin + public transport API
# ---------------------------------------------------------------------------
write(
    "apps/api/src/customers_manager_hub/website_chat.py",
    r'''
    import hashlib
    import secrets
    from datetime import UTC, datetime, timedelta
    from typing import Annotated, cast
    from uuid import UUID, uuid4

    from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
    from pydantic import BaseModel, Field, field_validator
    from redis.exceptions import RedisError
    from sqlalchemy import delete, select
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession

    from customers_manager_hub.channel_gateway import CanonicalInboundMessage
    from customers_manager_hub.channel_models import (
        ChannelAccount,
        ChannelAccountCapability,
        ChannelCapability,
        ChannelType,
        ConversationChannelBinding,
        WebsiteChannelOrigin,
        WebsiteChatSession,
    )
    from customers_manager_hub.channel_queue import ChannelJobQueue
    from customers_manager_hub.channel_runtime import (
        load_channel_account,
        mark_event_enqueued,
        persist_inbound_event,
    )
    from customers_manager_hub.database import get_db_session
    from customers_manager_hub.models import (
        AuditEvent,
        Message,
        MessageAuthorType,
        MessageDirection,
        MessageType,
        TenantRole,
    )
    from customers_manager_hub.tenants import TenantContextDependency, require_tenant_role
    from customers_manager_hub.website import normalize_website_origin

    admin_router = APIRouter(
        prefix="/api/v1/tenants/{tenant_id}/channels",
        tags=["website-chat-admin"],
    )
    public_router = APIRouter(prefix="/api/v1/public/website", tags=["website-chat-public"])
    DbSession = Annotated[AsyncSession, Depends(get_db_session)]
    ConfigWriteRole = frozenset({TenantRole.OWNER, TenantRole.ADMIN})
    _SESSION_LIFETIME = timedelta(days=30)
    _SESSION_TOKEN_HEADER = "x-website-session-token"
    _MAX_PUBLIC_MESSAGES = 100


    def get_channel_queue(request: Request) -> ChannelJobQueue:
        return cast(ChannelJobQueue, request.app.state.channel_queue)


    class WebsiteChannelCreate(BaseModel):
        name: str = Field(min_length=1, max_length=200)
        allowed_origins: list[str] = Field(min_length=1, max_length=20)

        @field_validator("name")
        @classmethod
        def normalize_name(cls, value: str) -> str:
            normalized = value.strip()
            if not normalized:
                raise ValueError("Channel name must not be blank")
            return normalized

        @field_validator("allowed_origins")
        @classmethod
        def normalize_origins(cls, value: list[str]) -> list[str]:
            normalized = [normalize_website_origin(item) for item in value]
            if len(normalized) != len(set(normalized)):
                raise ValueError("Website origins must be unique")
            return normalized


    class WebsiteOriginsUpdate(BaseModel):
        allowed_origins: list[str] = Field(min_length=1, max_length=20)

        @field_validator("allowed_origins")
        @classmethod
        def normalize_origins(cls, value: list[str]) -> list[str]:
            normalized = [normalize_website_origin(item) for item in value]
            if len(normalized) != len(set(normalized)):
                raise ValueError("Website origins must be unique")
            return normalized


    class WebsiteChannelResponse(BaseModel):
        id: UUID
        channel_type: ChannelType
        name: str
        is_active: bool
        supported_capabilities: list[ChannelCapability]
        allowed_origins: list[str]


    class WebsiteSessionResponse(BaseModel):
        session_id: UUID
        session_token: str
        expires_at: datetime


    class WebsiteMessageCreate(BaseModel):
        client_message_id: UUID
        text: str = Field(min_length=1, max_length=4096)

        @field_validator("text")
        @classmethod
        def normalize_text(cls, value: str) -> str:
            normalized = value.strip()
            if not normalized:
                raise ValueError("Message text must not be blank")
            if "\x00" in normalized:
                raise ValueError("Message text contains an invalid character")
            return normalized


    class WebsiteMessageAccepted(BaseModel):
        accepted: bool
        event_id: UUID


    class WebsitePublicMessage(BaseModel):
        id: UUID
        direction: MessageDirection
        author_type: MessageAuthorType
        text: str
        occurred_at: datetime


    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()


    def _set_cors(response: Response, origin: str) -> None:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"


    async def _load_website_account(
        db: AsyncSession,
        account_id: UUID,
        *,
        tenant_id: UUID | None = None,
        require_active: bool = False,
    ) -> ChannelAccount:
        account = await load_channel_account(db, account_id, tenant_id=tenant_id)
        if account is None or account.channel_type != ChannelType.WEBSITE.value:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website channel not found")
        if require_active and not account.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Website channel not found")
        return account


    async def _load_origins(
        db: AsyncSession,
        tenant_id: UUID,
        account_ids: list[UUID],
    ) -> dict[UUID, list[str]]:
        result = {account_id: [] for account_id in account_ids}
        if not account_ids:
            return result
        rows = list(
            (
                await db.scalars(
                    select(WebsiteChannelOrigin)
                    .where(
                        WebsiteChannelOrigin.tenant_id == tenant_id,
                        WebsiteChannelOrigin.channel_account_id.in_(account_ids),
                    )
                    .order_by(WebsiteChannelOrigin.origin)
                )
            ).all()
        )
        for row in rows:
            result[row.channel_account_id].append(row.origin)
        return result


    def _website_response(account: ChannelAccount, origins: list[str]) -> WebsiteChannelResponse:
        return WebsiteChannelResponse(
            id=account.id,
            channel_type=ChannelType.WEBSITE,
            name=account.name,
            is_active=account.is_active,
            supported_capabilities=[ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT],
            allowed_origins=origins,
        )


    async def _require_allowed_origin(
        db: AsyncSession,
        account: ChannelAccount,
        request: Request,
    ) -> str:
        supplied = request.headers.get("origin")
        if supplied is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is required")
        try:
            normalized = normalize_website_origin(supplied)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed") from exc
        allowed = await db.scalar(
            select(WebsiteChannelOrigin.id).where(
                WebsiteChannelOrigin.tenant_id == account.tenant_id,
                WebsiteChannelOrigin.channel_account_id == account.id,
                WebsiteChannelOrigin.origin == normalized,
            )
        )
        if allowed is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin is not allowed")
        return normalized


    async def _require_session(
        db: AsyncSession,
        account: ChannelAccount,
        session_id: UUID,
        request: Request,
    ) -> WebsiteChatSession:
        presented = request.headers.get(_SESSION_TOKEN_HEADER)
        if presented is None or len(presented) > 512:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Website session")
        chat_session = await db.scalar(
            select(WebsiteChatSession).where(
                WebsiteChatSession.id == session_id,
                WebsiteChatSession.tenant_id == account.tenant_id,
                WebsiteChatSession.channel_account_id == account.id,
            )
        )
        if chat_session is None or chat_session.expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Website session")
        if not secrets.compare_digest(chat_session.token_hash, _token_hash(presented)):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Website session")
        return chat_session


    @admin_router.post("/website", response_model=WebsiteChannelResponse, status_code=201)
    async def create_website_channel(
        payload: WebsiteChannelCreate,
        context: TenantContextDependency,
        db: DbSession,
    ) -> WebsiteChannelResponse:
        require_tenant_role(context, ConfigWriteRole)
        account = ChannelAccount(
            tenant_id=context.tenant.id,
            channel_type=ChannelType.WEBSITE.value,
            name=payload.name,
            external_account_id=f"website-{uuid4().hex}",
            external_username=None,
            is_active=True,
        )
        db.add(account)
        try:
            await db.flush()
            db.add(
                ChannelAccountCapability(
                    tenant_id=context.tenant.id,
                    channel_account_id=account.id,
                    capability=ChannelCapability.TEXT.value,
                    enabled=True,
                )
            )
            for origin in payload.allowed_origins:
                db.add(
                    WebsiteChannelOrigin(
                        tenant_id=context.tenant.id,
                        channel_account_id=account.id,
                        origin=origin,
                    )
                )
            db.add(
                AuditEvent(
                    tenant_id=context.tenant.id,
                    actor_user_id=context.current.user.id,
                    action="channel.website.created",
                    target_type="channel_account",
                    target_id=account.id,
                    details={"origin_count": len(payload.allowed_origins)},
                )
            )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Website channel configuration conflicts with an existing resource",
            ) from exc
        await db.refresh(account)
        return _website_response(account, sorted(payload.allowed_origins))


    @admin_router.get("/website", response_model=list[WebsiteChannelResponse])
    async def list_website_channels(
        context: TenantContextDependency,
        db: DbSession,
    ) -> list[WebsiteChannelResponse]:
        accounts = list(
            (
                await db.scalars(
                    select(ChannelAccount)
                    .where(
                        ChannelAccount.tenant_id == context.tenant.id,
                        ChannelAccount.channel_type == ChannelType.WEBSITE.value,
                    )
                    .order_by(ChannelAccount.created_at, ChannelAccount.id)
                )
            ).all()
        )
        origins = await _load_origins(db, context.tenant.id, [account.id for account in accounts])
        return [_website_response(account, origins[account.id]) for account in accounts]


    @admin_router.get("/website/{channel_account_id}", response_model=WebsiteChannelResponse)
    async def get_website_channel(
        channel_account_id: UUID,
        context: TenantContextDependency,
        db: DbSession,
    ) -> WebsiteChannelResponse:
        account = await _load_website_account(
            db,
            channel_account_id,
            tenant_id=context.tenant.id,
        )
        origins = await _load_origins(db, context.tenant.id, [account.id])
        return _website_response(account, origins[account.id])


    @admin_router.put(
        "/website/{channel_account_id}/origins",
        response_model=WebsiteChannelResponse,
    )
    async def update_website_origins(
        channel_account_id: UUID,
        payload: WebsiteOriginsUpdate,
        context: TenantContextDependency,
        db: DbSession,
    ) -> WebsiteChannelResponse:
        require_tenant_role(context, ConfigWriteRole)
        account = await _load_website_account(
            db,
            channel_account_id,
            tenant_id=context.tenant.id,
        )
        await db.execute(
            delete(WebsiteChannelOrigin).where(
                WebsiteChannelOrigin.tenant_id == context.tenant.id,
                WebsiteChannelOrigin.channel_account_id == account.id,
            )
        )
        for origin in payload.allowed_origins:
            db.add(
                WebsiteChannelOrigin(
                    tenant_id=context.tenant.id,
                    channel_account_id=account.id,
                    origin=origin,
                )
            )
        db.add(
            AuditEvent(
                tenant_id=context.tenant.id,
                actor_user_id=context.current.user.id,
                action="channel.website.origins.updated",
                target_type="channel_account",
                target_id=account.id,
                details={"origin_count": len(payload.allowed_origins)},
            )
        )
        await db.commit()
        await db.refresh(account)
        return _website_response(account, sorted(payload.allowed_origins))


    @public_router.options("/{channel_account_id}/{path:path}", status_code=204)
    async def website_preflight(
        channel_account_id: UUID,
        path: str,
        request: Request,
        db: DbSession,
    ) -> Response:
        del path
        account = await _load_website_account(db, channel_account_id, require_active=True)
        origin = await _require_allowed_origin(db, account, request)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        _set_cors(response, origin)
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Website-Session-Token"
        )
        response.headers["Access-Control-Max-Age"] = "600"
        return response


    @public_router.post(
        "/{channel_account_id}/sessions",
        response_model=WebsiteSessionResponse,
        status_code=201,
    )
    async def create_website_session(
        channel_account_id: UUID,
        request: Request,
        response: Response,
        db: DbSession,
    ) -> WebsiteSessionResponse:
        account = await _load_website_account(db, channel_account_id, require_active=True)
        origin = await _require_allowed_origin(db, account, request)
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + _SESSION_LIFETIME
        chat_session = WebsiteChatSession(
            tenant_id=account.tenant_id,
            channel_account_id=account.id,
            visitor_id=uuid4(),
            token_hash=_token_hash(raw_token),
            expires_at=expires_at,
        )
        db.add(chat_session)
        await db.commit()
        await db.refresh(chat_session)
        _set_cors(response, origin)
        return WebsiteSessionResponse(
            session_id=chat_session.id,
            session_token=raw_token,
            expires_at=chat_session.expires_at,
        )


    @public_router.post(
        "/{channel_account_id}/sessions/{session_id}/messages",
        response_model=WebsiteMessageAccepted,
        status_code=202,
    )
    async def post_website_message(
        channel_account_id: UUID,
        session_id: UUID,
        payload: WebsiteMessageCreate,
        request: Request,
        response: Response,
        db: DbSession,
        queue: Annotated[ChannelJobQueue, Depends(get_channel_queue)],
    ) -> WebsiteMessageAccepted:
        account = await _load_website_account(db, channel_account_id, require_active=True)
        origin = await _require_allowed_origin(db, account, request)
        chat_session = await _require_session(db, account, session_id, request)
        now = datetime.now(UTC)
        canonical = CanonicalInboundMessage(
            channel_type=ChannelType.WEBSITE,
            channel_account_id=account.id,
            external_event_id=f"website:{chat_session.id}:{payload.client_message_id}",
            external_message_id=str(payload.client_message_id),
            external_thread_id=str(chat_session.id),
            sender_namespace="website:visitor",
            sender_external_id=str(chat_session.visitor_id),
            sender_display_name=None,
            message_type=MessageType.TEXT,
            text=payload.text,
            occurred_at=now,
            metadata={"website_session_id": str(chat_session.id)},
        )
        persisted = await persist_inbound_event(
            db,
            account,
            external_event_id=canonical.external_event_id,
            canonical=canonical,
            ignored_reason=None,
        )
        if persisted.should_enqueue:
            try:
                await queue.enqueue(persisted.event_id)
                await mark_event_enqueued(db, persisted.event_id)
            except RedisError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE) from exc
        _set_cors(response, origin)
        return WebsiteMessageAccepted(accepted=True, event_id=persisted.event_id)


    @public_router.get(
        "/{channel_account_id}/sessions/{session_id}/messages",
        response_model=list[WebsitePublicMessage],
    )
    async def list_website_messages(
        channel_account_id: UUID,
        session_id: UUID,
        request: Request,
        response: Response,
        db: DbSession,
    ) -> list[WebsitePublicMessage]:
        account = await _load_website_account(db, channel_account_id, require_active=True)
        origin = await _require_allowed_origin(db, account, request)
        await _require_session(db, account, session_id, request)
        binding = await db.scalar(
            select(ConversationChannelBinding).where(
                ConversationChannelBinding.tenant_id == account.tenant_id,
                ConversationChannelBinding.channel_account_id == account.id,
                ConversationChannelBinding.external_thread_id == str(session_id),
            )
        )
        _set_cors(response, origin)
        if binding is None:
            return []
        rows = list(
            (
                await db.scalars(
                    select(Message)
                    .where(
                        Message.tenant_id == account.tenant_id,
                        Message.conversation_id == binding.conversation_id,
                        Message.message_type == MessageType.TEXT.value,
                        Message.text.is_not(None),
                    )
                    .order_by(Message.occurred_at, Message.created_at, Message.id)
                    .limit(_MAX_PUBLIC_MESSAGES)
                )
            ).all()
        )
        return [
            WebsitePublicMessage(
                id=item.id,
                direction=MessageDirection(item.direction),
                author_type=MessageAuthorType(item.author_type),
                text=cast(str, item.text),
                occurred_at=item.occurred_at,
            )
            for item in rows
        ]
    ''',
)

# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------
write(
    "apps/api/migrations/versions/0006_website_chat_channel.py",
    r'''
    """Website channel origins and public chat sessions.

    Revision ID: 0006_website_chat_channel
    Revises: 0005_agent_prompt_runtime
    Create Date: 2026-09-07
    """

    from collections.abc import Sequence

    import sqlalchemy as sa
    from alembic import op
    from sqlalchemy.dialects import postgresql

    revision: str = "0006_website_chat_channel"
    down_revision: str | None = "0005_agent_prompt_runtime"
    branch_labels: str | Sequence[str] | None = None
    depends_on: str | Sequence[str] | None = None


    def upgrade() -> None:
        op.drop_constraint("ck_channel_accounts_type", "channel_accounts", type_="check")
        op.create_check_constraint(
            "ck_channel_accounts_type",
            "channel_accounts",
            "channel_type IN ('telegram', 'website')",
        )

        op.create_table(
            "website_channel_origins",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("origin", sa.String(length=512), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["channel_account_id", "tenant_id"],
                ["channel_accounts.id", "channel_accounts.tenant_id"],
                ondelete="CASCADE",
                name="fk_website_channel_origins_account_tenant",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "channel_account_id",
                "origin",
                name="uq_website_channel_origins_account_origin",
            ),
        )
        op.create_index(
            "ix_website_channel_origins_tenant_account",
            "website_channel_origins",
            ["tenant_id", "channel_account_id"],
        )

        op.create_table(
            "website_chat_sessions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("channel_account_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("visitor_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("token_hash", sa.LargeBinary(length=32), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["channel_account_id", "tenant_id"],
                ["channel_accounts.id", "channel_accounts.tenant_id"],
                ondelete="CASCADE",
                name="fk_website_chat_sessions_account_tenant",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "channel_account_id",
                "visitor_id",
                name="uq_website_chat_sessions_account_visitor",
            ),
            sa.UniqueConstraint("token_hash", name="uq_website_chat_sessions_token_hash"),
        )
        op.create_index(
            "ix_website_chat_sessions_tenant_account",
            "website_chat_sessions",
            ["tenant_id", "channel_account_id", "created_at"],
        )
        op.create_index(
            "ix_website_chat_sessions_expires",
            "website_chat_sessions",
            ["expires_at"],
        )


    def downgrade() -> None:
        op.drop_index("ix_website_chat_sessions_expires", table_name="website_chat_sessions")
        op.drop_index(
            "ix_website_chat_sessions_tenant_account",
            table_name="website_chat_sessions",
        )
        op.drop_table("website_chat_sessions")
        op.drop_index(
            "ix_website_channel_origins_tenant_account",
            table_name="website_channel_origins",
        )
        op.drop_table("website_channel_origins")
        op.drop_constraint("ck_channel_accounts_type", "channel_accounts", type_="check")
        op.create_check_constraint(
            "ck_channel_accounts_type",
            "channel_accounts",
            "channel_type IN ('telegram')",
        )
    ''',
)

# ---------------------------------------------------------------------------
# Generic channel/agent runtime generalization
# ---------------------------------------------------------------------------
replace_once(
    "apps/api/src/customers_manager_hub/channels.py",
    '''    required_credentials = {
        ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
        ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
    }
''',
    '''    required_credentials = (
        {
            ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
            ChannelCredentialKind.TELEGRAM_WEBHOOK_SECRET,
        }
        if channel_type == ChannelType.TELEGRAM
        else set()
    )
''',
)
replace_once(
    "apps/api/src/customers_manager_hub/channels.py",
    '''    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    changed_fields: list[str] = []
''',
    '''    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found")
    if payload.enabled_inbound_types is not None:
        supported_inbound = registry.get(ChannelType(account.channel_type)).capabilities & _INBOUND_CAPABILITIES
        unsupported = set(payload.enabled_inbound_types) - supported_inbound
        if unsupported:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Inbound capability is not supported by this channel",
            )
    changed_fields: list[str] = []
''',
)

replace_once(
    "apps/api/src/customers_manager_hub/channel_runtime.py",
    "from dataclasses import dataclass\nfrom uuid import UUID\n",
    "from dataclasses import dataclass\nfrom datetime import UTC, datetime\nfrom uuid import UUID, uuid4\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/channel_runtime.py",
    '''    idempotency_key = (
        f"telegram:{account.id}:chat:{canonical.external_thread_id}:"
        f"message:{canonical.external_message_id}"
    )
''',
    '''    if canonical.channel_type == ChannelType.TELEGRAM:
        idempotency_key = (
            f"telegram:{account.id}:chat:{canonical.external_thread_id}:"
            f"message:{canonical.external_message_id}"
        )
    elif canonical.channel_type == ChannelType.WEBSITE:
        idempotency_key = (
            f"website:{account.id}:session:{canonical.external_thread_id}:"
            f"message:{canonical.external_message_id}"
        )
    else:
        raise ChannelRuntimeError("channel_inbound_adapter_unsupported")
''',
)
replace_once(
    "apps/api/src/customers_manager_hub/channel_runtime.py",
    '''    if account.channel_type != ChannelType.TELEGRAM.value:
        raise ChannelRuntimeError("channel_text_adapter_unsupported")

    access_secret = await load_channel_secret(
        db,
        settings,
        account,
        ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
    )
    adapter = registry.get(ChannelType.TELEGRAM)
    sent = await adapter.send_text(
        access_secret,
        external_thread_id=binding.external_thread_id,
        text=normalized_text,
    )
    identity = await db.scalar(
        select(ExternalIdentity).where(
            ExternalIdentity.tenant_id == tenant_id,
            ExternalIdentity.contact_id == conversation.contact_id,
            ExternalIdentity.namespace == "telegram:user",
            ExternalIdentity.external_id == binding.external_thread_id,
        )
    )
''',
    '''    identity: ExternalIdentity | None = None
    if account.channel_type == ChannelType.TELEGRAM.value:
        access_secret = await load_channel_secret(
            db,
            settings,
            account,
            ChannelCredentialKind.TELEGRAM_BOT_TOKEN,
        )
        adapter = registry.get(ChannelType.TELEGRAM)
        sent = await adapter.send_text(
            access_secret,
            external_thread_id=binding.external_thread_id,
            text=normalized_text,
        )
        external_message_id = sent.external_message_id
        occurred_at = sent.occurred_at
        identity = await db.scalar(
            select(ExternalIdentity).where(
                ExternalIdentity.tenant_id == tenant_id,
                ExternalIdentity.contact_id == conversation.contact_id,
                ExternalIdentity.namespace == "telegram:user",
                ExternalIdentity.external_id == binding.external_thread_id,
            )
        )
    elif account.channel_type == ChannelType.WEBSITE.value:
        external_message_id = f"website:{uuid4()}"
        occurred_at = datetime.now(UTC)
    else:
        raise ChannelRuntimeError("channel_text_adapter_unsupported")
''',
)
replace_once(
    "apps/api/src/customers_manager_hub/channel_runtime.py",
    "        external_message_id=sent.external_message_id,\n",
    "        external_message_id=external_message_id,\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/channel_runtime.py",
    "        occurred_at=sent.occurred_at,\n",
    "        occurred_at=occurred_at,\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/channel_runtime.py",
    "    if conversation.last_message_at is None or sent.occurred_at > conversation.last_message_at:\n        conversation.last_message_at = sent.occurred_at\n",
    "    if conversation.last_message_at is None or occurred_at > conversation.last_message_at:\n        conversation.last_message_at = occurred_at\n",
)

replace_once(
    "apps/api/src/customers_manager_hub/agent_runtime.py",
    '''    if channel_type == ChannelType.TELEGRAM:
        return (
            "Channel: Telegram private chat. Return plain text only. "
            "The final response must be no longer than 4096 characters."
        )
    raise AgentRuntimeError("agent_channel_unsupported", retryable=False)
''',
    '''    if channel_type == ChannelType.TELEGRAM:
        return (
            "Channel: Telegram private chat. Return plain text only. "
            "The final response must be no longer than 4096 characters."
        )
    if channel_type == ChannelType.WEBSITE:
        return (
            "Channel: Website chat. Return plain text only. "
            "The final response must be no longer than 4096 characters."
        )
    raise AgentRuntimeError("agent_channel_unsupported", retryable=False)
''',
)

# ---------------------------------------------------------------------------
# API/worker registration
# ---------------------------------------------------------------------------
replace_once(
    "apps/api/src/customers_manager_hub/main.py",
    "from customers_manager_hub.tenants import router as tenants_router\n",
    "from customers_manager_hub.tenants import router as tenants_router\n"
    "from customers_manager_hub.website import WebsiteAdapter\n"
    "from customers_manager_hub.website_chat import admin_router as website_admin_router\n"
    "from customers_manager_hub.website_chat import public_router as website_public_router\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/main.py",
    "                channel_registry = ChannelRegistry((TelegramAdapter(external_http_client),))\n",
    "                channel_registry = ChannelRegistry(\n"
    "                    (TelegramAdapter(external_http_client), WebsiteAdapter())\n"
    "                )\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/main.py",
    "    application.include_router(agents_router)\n    application.include_router(channels_router)\n",
    "    application.include_router(agents_router)\n"
    "    application.include_router(website_admin_router)\n"
    "    application.include_router(channels_router)\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/main.py",
    "    application.include_router(channel_webhooks_router)\n    return application\n",
    "    application.include_router(channel_webhooks_router)\n"
    "    application.include_router(website_public_router)\n"
    "    return application\n",
)

replace_once(
    "apps/api/src/customers_manager_hub/worker.py",
    "from customers_manager_hub.telegram import TelegramAdapter\n",
    "from customers_manager_hub.telegram import TelegramAdapter\n"
    "from customers_manager_hub.website import WebsiteAdapter\n",
)
replace_once(
    "apps/api/src/customers_manager_hub/worker.py",
    "            channel_registry = ChannelRegistry((TelegramAdapter(external_http_client),))\n",
    "            channel_registry = ChannelRegistry(\n"
    "                (TelegramAdapter(external_http_client), WebsiteAdapter())\n"
    "            )\n",
)

# ---------------------------------------------------------------------------
# Lightweight Webchat artifact
# ---------------------------------------------------------------------------
write(
    "apps/webchat/README.md",
    r'''
    # Customers Manager HUB Webchat

    `apps/webchat` is the lightweight customer-facing Website Chat artifact. It is intentionally
    separate from the Next.js Admin Console (`apps/web`) and has no framework/runtime dependency.

    ## Embed

    Host `widget.js` from your static asset/CDN origin, then embed it on an origin configured for the
    Website channel account:

    ```html
    <script
      src="https://static.example.com/customers-manager-hub/widget.js"
      data-api-base="https://hub.example.com"
      data-channel-account-id="00000000-0000-0000-0000-000000000000"
      defer
    ></script>
    ```

    The widget creates a Shadow DOM floating chat UI, obtains a scoped public Website session,
    persists the session ID/token in browser local storage when available, posts retry-safe UUID
    message IDs, and polls the canonical public-message endpoint while the panel is open.

    Do not place Admin credentials, tenant secrets, AI provider keys, or business API credentials in
    widget attributes or source.
    ''',
)
write(
    "apps/webchat/widget.js",
    r'''
    (() => {
      "use strict";

      const script = document.currentScript;
      if (!(script instanceof HTMLScriptElement)) return;

      const apiBase = (script.dataset.apiBase || "").replace(/\/$/, "");
      const channelAccountId = script.dataset.channelAccountId || "";
      if (!apiBase || !channelAccountId) {
        console.error("Customers Manager HUB Webchat requires data-api-base and data-channel-account-id");
        return;
      }

      const storageKey = `cmh:webchat:${channelAccountId}`;
      let session = null;
      let pollTimer = null;
      const rendered = new Set();

      try {
        const stored = localStorage.getItem(storageKey);
        if (stored) session = JSON.parse(stored);
      } catch (_) {
        session = null;
      }

      const host = document.createElement("div");
      const root = host.attachShadow({ mode: "open" });
      const style = document.createElement("style");
      style.textContent = `
        :host { all: initial; }
        .launcher { position: fixed; right: 20px; bottom: 20px; z-index: 2147483000; border: 0;
          border-radius: 999px; padding: 12px 16px; background: #111827; color: #fff; cursor: pointer;
          font: 600 14px system-ui, sans-serif; box-shadow: 0 8px 30px rgba(0,0,0,.18); }
        .panel { position: fixed; right: 20px; bottom: 76px; z-index: 2147483000; width: min(360px, calc(100vw - 32px));
          height: min(520px, calc(100vh - 110px)); display: none; flex-direction: column; overflow: hidden;
          border: 1px solid rgba(17,24,39,.12); border-radius: 16px; background: #fff;
          box-shadow: 0 18px 60px rgba(0,0,0,.22); font: 14px system-ui, sans-serif; color: #111827; }
        .panel.open { display: flex; }
        .header { padding: 14px 16px; font-weight: 700; border-bottom: 1px solid #e5e7eb; }
        .messages { flex: 1; overflow: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; }
        .message { max-width: 82%; padding: 9px 11px; border-radius: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
        .inbound { align-self: flex-end; background: #111827; color: #fff; }
        .outbound { align-self: flex-start; background: #f3f4f6; color: #111827; }
        .composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e7eb; }
        input { min-width: 0; flex: 1; border: 1px solid #d1d5db; border-radius: 10px; padding: 9px 10px;
          font: inherit; color: inherit; background: #fff; }
        .send { border: 0; border-radius: 10px; padding: 9px 12px; background: #111827; color: #fff; cursor: pointer; }
        .status { min-height: 18px; padding: 0 12px 8px; color: #6b7280; font-size: 12px; }
      `;

      const panel = document.createElement("section");
      panel.className = "panel";
      const header = document.createElement("div");
      header.className = "header";
      header.textContent = "Chat";
      const messages = document.createElement("div");
      messages.className = "messages";
      const form = document.createElement("form");
      form.className = "composer";
      const input = document.createElement("input");
      input.type = "text";
      input.maxLength = 4096;
      input.placeholder = "Type a message…";
      input.autocomplete = "off";
      const send = document.createElement("button");
      send.type = "submit";
      send.className = "send";
      send.textContent = "Send";
      const statusNode = document.createElement("div");
      statusNode.className = "status";
      const launcher = document.createElement("button");
      launcher.type = "button";
      launcher.className = "launcher";
      launcher.textContent = "Chat";

      form.append(input, send);
      panel.append(header, messages, form, statusNode);
      root.append(style, panel, launcher);
      document.body.append(host);

      const setStatus = (value) => { statusNode.textContent = value || ""; };

      async function ensureSession() {
        if (session?.session_id && session?.session_token) return session;
        const response = await fetch(`${apiBase}/api/v1/public/website/${channelAccountId}/sessions`, {
          method: "POST",
          mode: "cors",
          credentials: "omit",
        });
        if (!response.ok) throw new Error("Unable to start chat session");
        session = await response.json();
        try { localStorage.setItem(storageKey, JSON.stringify(session)); } catch (_) {}
        return session;
      }

      function renderMessage(item) {
        if (!item?.id || rendered.has(item.id) || typeof item.text !== "string") return;
        rendered.add(item.id);
        const node = document.createElement("div");
        node.className = `message ${item.direction === "inbound" ? "inbound" : "outbound"}`;
        node.textContent = item.text;
        messages.append(node);
        messages.scrollTop = messages.scrollHeight;
      }

      async function poll() {
        try {
          const current = await ensureSession();
          const response = await fetch(
            `${apiBase}/api/v1/public/website/${channelAccountId}/sessions/${current.session_id}/messages`,
            {
              method: "GET",
              mode: "cors",
              credentials: "omit",
              headers: { "X-Website-Session-Token": current.session_token },
            },
          );
          if (response.status === 401) {
            session = null;
            rendered.clear();
            try { localStorage.removeItem(storageKey); } catch (_) {}
            return;
          }
          if (!response.ok) throw new Error("Unable to load messages");
          const items = await response.json();
          for (const item of items) renderMessage(item);
          setStatus("");
        } catch (_) {
          setStatus("Connection unavailable. Retrying…");
        }
      }

      function startPolling() {
        if (pollTimer) return;
        void poll();
        pollTimer = window.setInterval(() => { void poll(); }, 1500);
      }

      function stopPolling() {
        if (!pollTimer) return;
        window.clearInterval(pollTimer);
        pollTimer = null;
      }

      launcher.addEventListener("click", () => {
        const opening = !panel.classList.contains("open");
        panel.classList.toggle("open", opening);
        launcher.textContent = opening ? "Close" : "Chat";
        if (opening) {
          startPolling();
          input.focus();
        } else {
          stopPolling();
        }
      });

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const text = input.value.trim();
        if (!text) return;
        send.disabled = true;
        try {
          const current = await ensureSession();
          const response = await fetch(
            `${apiBase}/api/v1/public/website/${channelAccountId}/sessions/${current.session_id}/messages`,
            {
              method: "POST",
              mode: "cors",
              credentials: "omit",
              headers: {
                "Content-Type": "application/json",
                "X-Website-Session-Token": current.session_token,
              },
              body: JSON.stringify({ client_message_id: crypto.randomUUID(), text }),
            },
          );
          if (!response.ok) throw new Error("Unable to send message");
          input.value = "";
          setStatus("");
          await poll();
        } catch (_) {
          setStatus("Message was not sent. Try again.");
        } finally {
          send.disabled = false;
          input.focus();
        }
      });
    })();
    ''',
)

# ---------------------------------------------------------------------------
# Unit + integration coverage
# ---------------------------------------------------------------------------
write(
    "apps/api/tests/test_website.py",
    r'''
    import pytest

    from customers_manager_hub.agent_runtime import channel_instruction
    from customers_manager_hub.channel_models import ChannelCapability, ChannelType
    from customers_manager_hub.website import WebsiteAdapter, normalize_website_origin


    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://Example.COM", "https://example.com"),
            ("https://example.com/", "https://example.com"),
            ("https://example.com:8443", "https://example.com:8443"),
            ("http://localhost:3000", "http://localhost:3000"),
            ("http://127.0.0.1", "http://127.0.0.1"),
        ],
    )
    def test_normalize_website_origin(raw: str, expected: str) -> None:
        assert normalize_website_origin(raw) == expected


    @pytest.mark.parametrize(
        "raw",
        [
            "*",
            "null",
            "http://example.com",
            "https://example.com/path",
            "https://example.com/?query=1",
            "https://user@example.com",
            "ftp://example.com",
        ],
    )
    def test_invalid_website_origin(raw: str) -> None:
        with pytest.raises(ValueError):
            normalize_website_origin(raw)


    def test_website_adapter_and_agent_instruction_are_channel_specific() -> None:
        adapter = WebsiteAdapter()
        assert adapter.channel_type == ChannelType.WEBSITE
        assert adapter.capabilities == frozenset(
            {ChannelCapability.TEXT, ChannelCapability.OUTBOUND_TEXT}
        )
        instruction = channel_instruction(ChannelType.WEBSITE)
        assert "Website chat" in instruction
        assert "4096" in instruction
    ''',
)

write(
    "apps/api/tests/test_website_chat_integration.py",
    r'''
    import asyncio
    import base64
    import os
    from collections.abc import Iterator
    from uuid import UUID, uuid4

    import pytest
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from httpx2 import Response
    from pydantic import SecretStr
    from redis import Redis
    from sqlalchemy import create_engine, func, select, text
    from sqlalchemy.orm import Session

    from customers_manager_hub.agent_models import AgentRun, AgentRunStatus
    from customers_manager_hub.ai_gateway import AIGateway, AIProviderRegistry, MockAIProviderAdapter
    from customers_manager_hub.ai_models import AITaskProfile, AITaskRoute, AITaskType
    from customers_manager_hub.channel_models import (
        ChannelAccount,
        ChannelInboundEvent,
        ChannelType,
        WebsiteChatSession,
    )
    from customers_manager_hub.channel_queue import (
        CHANNEL_JOB_STREAM,
        ChannelJobQueue,
        create_channel_redis,
    )
    from customers_manager_hub.config import Settings
    from customers_manager_hub.database import create_database
    from customers_manager_hub.main import create_app
    from customers_manager_hub.models import (
        AuditEvent,
        Message,
        MessageAuthorType,
        MessageDirection,
        PlatformUser,
        Tenant,
        TenantMembership,
        TenantRole,
    )
    from customers_manager_hub.security import hash_password
    from customers_manager_hub.website import WebsiteAdapter
    from customers_manager_hub.channel_gateway import ChannelRegistry
    from customers_manager_hub.worker import process_job

    RUN_DB_INTEGRATION = os.environ.get("RUN_DB_INTEGRATION") == "1"
    DATABASE_URL = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://cmh:change-me@localhost:5432/customers_manager_hub",
    )
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    TEST_SETTINGS = Settings(
        app_env="test",
        database_url=DATABASE_URL,
        redis_url=REDIS_URL,
        encryption_key=SecretStr(base64.urlsafe_b64encode(b"w" * 32).decode()),
    )
    SYNC_ENGINE = create_engine(TEST_SETTINGS.sqlalchemy_database_url)
    SYNC_REDIS = Redis.from_url(REDIS_URL, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]

    pytestmark = pytest.mark.skipif(
        not RUN_DB_INTEGRATION,
        reason="Website Chat integration tests require RUN_DB_INTEGRATION=1",
    )


    @pytest.fixture(autouse=True)
    def clean_runtime() -> Iterator[None]:
        with SYNC_ENGINE.begin() as connection:
            connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))
        SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
        yield
        SYNC_REDIS.delete(CHANNEL_JOB_STREAM)
        with SYNC_ENGINE.begin() as connection:
            connection.execute(text("TRUNCATE TABLE platform_users, tenants CASCADE"))


    def seed_tenant_user(
        *,
        slug: str,
        name: str,
        email: str,
        password: str,
        role: TenantRole = TenantRole.OWNER,
        tenant_id: UUID | None = None,
    ) -> UUID:
        with Session(SYNC_ENGINE, expire_on_commit=False) as db:
            if tenant_id is None:
                tenant = Tenant(slug=slug, name=name)
                db.add(tenant)
                db.flush()
                resolved_tenant_id = tenant.id
            else:
                resolved_tenant_id = tenant_id
            user = PlatformUser(email=email, password_hash=hash_password(password))
            db.add(user)
            db.flush()
            db.add(
                TenantMembership(
                    tenant_id=resolved_tenant_id,
                    user_id=user.id,
                    role=role.value,
                )
            )
            db.commit()
            return resolved_tenant_id


    def login(client: TestClient, email: str, password: str) -> Response:
        result = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert result.status_code == 200
        return result


    def open_client() -> tuple[FastAPI, TestClient]:
        app = create_app(TEST_SETTINGS)
        client = TestClient(app)
        client.__enter__()
        return app, client


    def close_client(client: TestClient) -> None:
        client.__exit__(None, None, None)


    def create_website_channel(client: TestClient, tenant_id: UUID) -> UUID:
        result = client.post(
            f"/api/v1/tenants/{tenant_id}/channels/website",
            json={
                "name": "Website Support",
                "allowed_origins": ["https://shop.example.test"],
            },
        )
        assert result.status_code == 201
        body = result.json()
        assert body["channel_type"] == "website"
        assert body["allowed_origins"] == ["https://shop.example.test"]
        return UUID(body["id"])


    def create_published_prompt(client: TestClient, tenant_id: UUID) -> UUID:
        created = client.post(
            f"/api/v1/tenants/{tenant_id}/prompts",
            json={"name": "Website Prompt", "content": "Answer Website visitors briefly."},
        )
        assert created.status_code == 201
        prompt_id = UUID(created.json()["id"])
        published = client.post(f"/api/v1/tenants/{tenant_id}/prompts/{prompt_id}/publish")
        assert published.status_code == 200
        return prompt_id


    def configure_agent(client: TestClient, tenant_id: UUID, channel_id: UUID) -> None:
        prompt_id = create_published_prompt(client, tenant_id)
        created = client.post(
            f"/api/v1/tenants/{tenant_id}/agents",
            json={"name": "Website Agent", "prompt_id": str(prompt_id)},
        )
        assert created.status_code == 201
        assigned = client.put(
            f"/api/v1/tenants/{tenant_id}/agents/assignments/channels/{channel_id}",
            json={"agent_id": created.json()["id"]},
        )
        assert assigned.status_code == 200
        with Session(SYNC_ENGINE) as db:
            profile = AITaskProfile(
                tenant_id=tenant_id,
                task_type=AITaskType.CUSTOMER_RESPONSE.value,
                timeout_seconds=30,
                attempts_per_route=1,
            )
            db.add(profile)
            db.flush()
            db.add(
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="mock",
                    model_id="mock-website-model",
                    priority=0,
                    parameters={},
                )
            )
            db.commit()


    async def process_one_website_job() -> None:
        engine, session_factory = create_database(TEST_SETTINGS)
        redis_client = create_channel_redis(TEST_SETTINGS)
        queue = ChannelJobQueue(redis_client)
        try:
            await queue.ensure_group()
            jobs = await queue.consume("website-consumer", block_ms=20)
            assert len(jobs) == 1
            await process_job(
                jobs[0],
                queue,
                ChannelRegistry((WebsiteAdapter(),)),
                AIGateway(
                    AIProviderRegistry(
                        (MockAIProviderAdapter(key="mock", generation_text="Website reply"),)
                    ),
                    session_factory,
                ),
                TEST_SETTINGS,
                session_factory,
            )
        finally:
            await redis_client.aclose()
            await engine.dispose()


    def test_website_origin_session_and_agent_pipeline() -> None:
        tenant_id = seed_tenant_user(
            slug="website-a",
            name="Website A",
            email="owner@example.com",
            password="owner website password",
        )
        viewer_tenant_id = seed_tenant_user(
            slug="website-b",
            name="Website B",
            email="viewer@example.com",
            password="viewer website password",
            role=TenantRole.VIEWER,
        )
        app, client = open_client()
        try:
            login(client, "owner@example.com", "owner website password")
            channel_id = create_website_channel(client, tenant_id)
            configure_agent(client, tenant_id, channel_id)

            generic = client.get(f"/api/v1/tenants/{tenant_id}/channels")
            assert generic.status_code == 200
            website_item = next(item for item in generic.json() if item["id"] == str(channel_id))
            assert website_item["credentials_configured"] is True
            assert website_item["supported_capabilities"] == ["outbound_text", "text"]

            rejected = client.post(
                f"/api/v1/public/website/{channel_id}/sessions",
                headers={"Origin": "https://evil.example.test"},
            )
            assert rejected.status_code == 403

            preflight = client.options(
                f"/api/v1/public/website/{channel_id}/sessions/placeholder/messages",
                headers={
                    "Origin": "https://shop.example.test",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,x-website-session-token",
                },
            )
            assert preflight.status_code == 204
            assert preflight.headers["access-control-allow-origin"] == "https://shop.example.test"

            created_session = client.post(
                f"/api/v1/public/website/{channel_id}/sessions",
                headers={"Origin": "https://shop.example.test"},
            )
            assert created_session.status_code == 201
            assert created_session.headers["access-control-allow-origin"] == "https://shop.example.test"
            session_id = UUID(created_session.json()["session_id"])
            session_token = created_session.json()["session_token"]

            with Session(SYNC_ENGINE) as db:
                stored = db.get(WebsiteChatSession, session_id)
                assert stored is not None
                assert stored.token_hash != session_token.encode()
                audit_text = repr(
                    [item.details for item in db.scalars(select(AuditEvent)).all()]
                )
                assert session_token not in audit_text

            wrong_token = client.post(
                f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
                headers={
                    "Origin": "https://shop.example.test",
                    "X-Website-Session-Token": "wrong-token",
                },
                json={"client_message_id": str(uuid4()), "text": "Hello"},
            )
            assert wrong_token.status_code == 401

            async def ensure_group() -> None:
                redis_client = create_channel_redis(TEST_SETTINGS)
                try:
                    await ChannelJobQueue(redis_client).ensure_group()
                finally:
                    await redis_client.aclose()

            asyncio.run(ensure_group())
            client_message_id = uuid4()
            accepted = client.post(
                f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
                headers={
                    "Origin": "https://shop.example.test",
                    "X-Website-Session-Token": session_token,
                },
                json={"client_message_id": str(client_message_id), "text": "Can you help?"},
            )
            assert accepted.status_code == 202
            assert accepted.headers["access-control-allow-origin"] == "https://shop.example.test"
            assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 1

            with Session(SYNC_ENGINE) as db:
                event = db.scalar(
                    select(ChannelInboundEvent).where(
                        ChannelInboundEvent.channel_account_id == channel_id
                    )
                )
                assert event is not None
                redis_payload = repr(SYNC_REDIS.xrange(CHANNEL_JOB_STREAM))
                assert session_token not in redis_payload

            asyncio.run(process_one_website_job())
            assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0

            messages = client.get(
                f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
                headers={
                    "Origin": "https://shop.example.test",
                    "X-Website-Session-Token": session_token,
                },
            )
            assert messages.status_code == 200
            assert [item["text"] for item in messages.json()] == ["Can you help?", "Website reply"]
            assert [item["direction"] for item in messages.json()] == ["inbound", "outbound"]
            serialized_public = repr(messages.json())
            assert str(tenant_id) not in serialized_public
            assert "website_session_id" not in serialized_public

            with Session(SYNC_ENGINE) as db:
                assert db.scalar(select(func.count()).select_from(Message)) == 2
                inbound = db.scalar(select(Message).where(Message.direction == MessageDirection.INBOUND.value))
                outbound = db.scalar(select(Message).where(Message.direction == MessageDirection.OUTBOUND.value))
                assert inbound is not None and outbound is not None
                assert inbound.author_type == MessageAuthorType.CUSTOMER.value
                assert outbound.author_type == MessageAuthorType.AI.value
                assert outbound.text == "Website reply"
                assert outbound.external_metadata["channel_type"] == ChannelType.WEBSITE.value
                run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id))
                assert run is not None
                assert run.status == AgentRunStatus.SUCCEEDED.value
                assert run.outbound_message_id == outbound.id

            retry = client.post(
                f"/api/v1/public/website/{channel_id}/sessions/{session_id}/messages",
                headers={
                    "Origin": "https://shop.example.test",
                    "X-Website-Session-Token": session_token,
                },
                json={"client_message_id": str(client_message_id), "text": "Can you help?"},
            )
            assert retry.status_code == 202
            assert SYNC_REDIS.xlen(CHANNEL_JOB_STREAM) == 0
            with Session(SYNC_ENGINE) as db:
                assert db.scalar(select(func.count()).select_from(Message)) == 2

            seed_tenant_user(
                slug="unused",
                name="unused",
                email="viewer2@example.com",
                password="viewer2 password",
                role=TenantRole.VIEWER,
                tenant_id=tenant_id,
            )
            login(client, "viewer2@example.com", "viewer2 password")
            denied = client.put(
                f"/api/v1/tenants/{tenant_id}/channels/website/{channel_id}/origins",
                json={"allowed_origins": ["https://other.example.test"]},
            )
            assert denied.status_code == 403

            login(client, "viewer@example.com", "viewer website password")
            cross = client.get(
                f"/api/v1/tenants/{viewer_tenant_id}/channels/website/{channel_id}"
            )
            assert cross.status_code == 404
        finally:
            close_client(client)
    ''',
)

print("Milestone 7 Website Chat patch applied")
