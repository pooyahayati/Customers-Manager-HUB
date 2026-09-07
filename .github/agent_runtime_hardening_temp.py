from pathlib import Path


runtime = Path("apps/api/src/customers_manager_hub/agent_runtime.py")
content = runtime.read_text()
content = content.replace(
    "import asyncio\n\nfrom dataclasses import dataclass\n",
    "import asyncio\nfrom contextlib import suppress\nfrom dataclasses import dataclass\n",
    1,
)
content = content.replace(
    '''        if not generation_task.done():
            generation_task.cancel()
            try:
                await generation_task
            except asyncio.CancelledError:
                pass
''',
    '''        if not generation_task.done():
            generation_task.cancel()
            with suppress(asyncio.CancelledError):
                await generation_task
''',
    1,
)
runtime.write_text(content)


test = Path("apps/api/tests/test_agent_prompt_integration.py")
content = test.read_text()

import_marker = '''from customers_manager_hub.agent_models import (
    AgentPromptVersion,
    AgentRun,
    AgentRunStatus,
    PromptVersionStatus,
)
'''
runtime_import = '''from customers_manager_hub.agent_runtime import (
    build_conversation_input,
    process_agent_event,
)
'''
if runtime_import not in content:
    if import_marker not in content:
        raise SystemExit("agent model import marker not found")
    content = content.replace(import_marker, import_marker + runtime_import, 1)

fixture_marker = '''

@pytest.fixture(autouse=True)
def clean_runtime() -> Iterator[None]:
'''
observing_class = '''

class LeaseObservingAIProvider(RecordingMockAIProvider):
    def __init__(self, *, generation_text: str) -> None:
        super().__init__(generation_text=generation_text)
        self.lease_samples: list[datetime] = []

    async def generate(
        self,
        model_id: str,
        request: GenerationRequest,
        parameters: dict[str, object],
        timeout_seconds: int,
    ) -> GenerationResult:
        with Session(SYNC_ENGINE) as db:
            first = db.scalar(select(AgentRun.lease_until))
            assert first is not None
            self.lease_samples.append(first)
        await asyncio.sleep(0.05)
        with Session(SYNC_ENGINE) as db:
            second = db.scalar(select(AgentRun.lease_until))
            assert second is not None
            self.lease_samples.append(second)
        return await super().generate(model_id, request, parameters, timeout_seconds)
'''
if "class LeaseObservingAIProvider(" not in content:
    if fixture_marker not in content:
        raise SystemExit("fixture marker not found")
    content = content.replace(fixture_marker, observing_class + fixture_marker, 1)

insert_before = '''
def test_retryable_ai_failure_is_reclaimed_and_terminal_output_failure_is_acked() -> None:
'''
new_test = r'''

def test_long_generation_renews_lease_and_context_budget_is_enforced() -> None:
    adapter = FakeTelegramAdapter()
    _, client = open_client(adapter)
    try:
        tenant_id, channel_id, _, webhook_secret = configure_runtime(
            client,
            adapter,
            prompt_content="Answer briefly.",
        )
        accepted = post_webhook(
            client,
            channel_id,
            webhook_secret,
            update_id=551,
            message_id=56,
            text_value="Please help with my order",
        )
        assert accepted.status_code == 200
        with Session(SYNC_ENGINE) as db:
            event = db.scalar(
                select(ChannelInboundEvent).where(
                    ChannelInboundEvent.channel_account_id == channel_id,
                    ChannelInboundEvent.external_event_id == "551",
                )
            )
            assert event is not None
            event_id = event.id

        ai_provider = LeaseObservingAIProvider(generation_text="I can help with that.")

        async def run_agent() -> None:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                await process_agent_event(
                    session_factory,
                    AIGateway(AIProviderRegistry((ai_provider,)), session_factory),
                    ChannelRegistry((adapter,)),
                    TEST_SETTINGS,
                    event_id,
                    lease_renew_interval_seconds=0.01,
                )
            finally:
                await engine.dispose()

        asyncio.run(run_agent())
        assert len(ai_provider.lease_samples) == 2
        assert ai_provider.lease_samples[1] > ai_provider.lease_samples[0]
        assert ai_provider.calls == 1
        assert adapter.send_attempts == 1

        with Session(SYNC_ENGINE) as db:
            run = db.scalar(select(AgentRun).where(AgentRun.tenant_id == tenant_id))
            assert run is not None
            assert run.status == AgentRunStatus.SUCCEEDED.value
            conversation_id = run.conversation_id
            inbound_message_id = run.inbound_message_id

        async def bounded_context() -> str:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                return await build_conversation_input(
                    session_factory,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    current_message_id=inbound_message_id,
                    current_text="Please help with my order",
                    history_char_budget=10,
                )
            finally:
                await engine.dispose()

        model_input = asyncio.run(bounded_context())
        history_section, current_section = model_input.split(
            "\n\nCurrent customer message:\n",
            1,
        )
        history_payload = history_section.split("\n", 1)[1]
        assert len(history_payload) <= 10
        assert current_section == "CUSTOMER: Please help with my order"
    finally:
        close_client(client)
'''
if "def test_long_generation_renews_lease_and_context_budget_is_enforced()" not in content:
    if insert_before not in content:
        raise SystemExit("retryable AI test marker not found")
    content = content.replace(insert_before, new_test + insert_before, 1)

test.write_text(content)


adr = Path("docs/adr/ADR-012-versioned-prompts-and-durable-agent-runs.md")
content = adr.read_text()
old_lease = "A short PostgreSQL-backed lease prevents two workers from generating/sending the same run concurrently. An expired lease can be reclaimed after a worker crash."
new_lease = "A short PostgreSQL-backed lease prevents two workers from generating/sending the same run concurrently. While AI generation remains in flight, the runtime periodically renews the lease without holding a database transaction open. An expired lease can be reclaimed after a worker crash."
if new_lease not in content:
    if old_lease not in content:
        raise SystemExit("ADR lease paragraph not found")
    content = content.replace(old_lease, new_lease, 1)

old_rejected = "### Hold a database transaction/advisory lock across the AI and Telegram network calls\n\nRejected. It would keep database transactions/connections open across slow external I/O and reduce runtime resilience."
new_rejected = "### Hold the AgentRun transaction/row lock across AI generation\n\nRejected. It would keep database transactions/connections open across slow AI I/O and reduce runtime resilience. AgentRun ownership instead uses a renewable lease. Outbound dispatch continues to use Milestone 5's existing per-idempotency advisory transaction serialization inside the channel runtime; ADR-012 does not redefine that transport boundary."
if new_rejected not in content:
    if old_rejected not in content:
        raise SystemExit("ADR rejected alternative paragraph not found")
    content = content.replace(old_rejected, new_rejected, 1)
adr.write_text(content)


spec = Path("specs/MILESTONE-6-AGENT-PROMPT-RUNTIME.md")
content = spec.read_text()
old_runtime = "A worker must not hold a database transaction open across AI/provider network calls. Instead it claims the run with a lease, performs external work, and commits state transitions between stages. An expired lease may be reclaimed."
new_runtime = "A worker must not hold a database transaction open across AI/provider network calls. Instead it claims the run with a lease, performs external work, and commits state transitions between stages. While AI generation remains in flight, the runtime periodically renews the lease using short independent database transactions. An expired lease may be reclaimed."
if new_runtime not in content:
    if old_runtime not in content:
        raise SystemExit("spec lease paragraph not found")
    content = content.replace(old_runtime, new_runtime, 1)

old_acceptance = "15. AI execution trace is still created through the configured task profile without storing prompt/customer/output content.\n16. Migration applies from `0004`, `alembic check` reports no drift, and full Docker Compose runtime still starts API/Web/Worker/PostgreSQL/Redis with API/Worker non-root."
new_acceptance = "15. Long-running AI generation renews the AgentRun lease so normal provider retry/fallback duration cannot cause a second worker to claim the same run.\n16. AI execution trace is still created through the configured task profile without storing prompt/customer/output content.\n17. Migration applies from `0004`, `alembic check` reports no drift, and full Docker Compose runtime still starts API/Web/Worker/PostgreSQL/Redis with API/Worker non-root."
if new_acceptance not in content:
    if old_acceptance not in content:
        raise SystemExit("spec acceptance criteria block not found")
    content = content.replace(old_acceptance, new_acceptance, 1)
spec.write_text(content)
