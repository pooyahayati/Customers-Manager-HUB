from pathlib import Path


ai_gateway = Path("apps/api/src/customers_manager_hub/ai_gateway.py")
content = ai_gateway.read_text()
content = content.replace(
    '''        last_error_code = "all_routes_failed"\n        last_error_retryable = False\n''',
    '''        last_error_code = "all_routes_failed"\n        any_retryable_failure = False\n''',
    1,
)
content = content.replace(
    '''            if adapter is None:\n                last_error_code = "provider_unavailable"\n                last_error_retryable = False\n''',
    '''            if adapter is None:\n                last_error_code = "provider_unavailable"\n''',
    1,
)
content = content.replace(
    '''            if not adapter.supports(operation):\n                last_error_code = "provider_capability_unavailable"\n                last_error_retryable = False\n''',
    '''            if not adapter.supports(operation):\n                last_error_code = "provider_capability_unavailable"\n''',
    1,
)
content = content.replace(
    '''                    last_error_code = exc.code\n                    last_error_retryable = exc.retryable\n''',
    '''                    last_error_code = exc.code\n                    any_retryable_failure = any_retryable_failure or exc.retryable\n''',
    1,
)
content = content.replace(
    '''        raise AIRoutingError(last_error_code, retryable=last_error_retryable)\n''',
    '''        raise AIRoutingError(last_error_code, retryable=any_retryable_failure)\n''',
    1,
)
ai_gateway.write_text(content)


runtime = Path("apps/api/src/customers_manager_hub/agent_runtime.py")
content = runtime.read_text()
old_budget = '''    selected_latest_first: list[str] = []
    used = 0
    for message in recent:
        text = (message.text or "").strip()
        if not text:
            continue
        text = text[:_CONTEXT_SINGLE_MESSAGE_LIMIT]
        snippet = f"{_history_label(message)}: {text}"
        remaining = history_char_budget - used
        if remaining <= 0:
            break
        if len(snippet) > remaining:
            if not selected_latest_first:
                selected_latest_first.append(snippet[:remaining])
            break
        selected_latest_first.append(snippet)
        used += len(snippet)

    history = list(reversed(selected_latest_first))
    history_text = "\\n".join(history) if history else "(no prior text messages)"
'''
new_budget = '''    selected_latest_first: list[str] = []
    used = 0
    for message in recent:
        text = (message.text or "").strip()
        if not text:
            continue
        text = text[:_CONTEXT_SINGLE_MESSAGE_LIMIT]
        snippet = f"{_history_label(message)}: {text}"
        separator_cost = 1 if selected_latest_first else 0
        remaining = history_char_budget - used - separator_cost
        if remaining <= 0:
            break
        if len(snippet) > remaining:
            if not selected_latest_first:
                selected_latest_first.append(snippet[:remaining])
                used += len(selected_latest_first[-1])
            break
        selected_latest_first.append(snippet)
        used += separator_cost + len(snippet)

    history = list(reversed(selected_latest_first))
    history_text = (
        "\\n".join(history)
        if history
        else "(no prior text messages)"[:history_char_budget]
    )
'''
if old_budget not in content:
    raise SystemExit("context budget block not found")
content = content.replace(old_budget, new_budget, 1)
runtime.write_text(content)


ai_test = Path("apps/api/tests/test_ai_gateway_integration.py")
content = ai_test.read_text()
content = content.replace(
    '''    AIGateway,\n    AIProviderRegistry,\n''',
    '''    AIGateway,\n    AIRoutingError,\n    AIProviderRegistry,\n''',
    1,
)
new_ai_test = '''

def test_retryable_failure_is_not_masked_by_later_unavailable_route() -> None:
    tenant_id, _ = seed_tenant_user(
        tenant_slug="retryability-tenant",
        tenant_name="Retryability Tenant",
        email="retryability@example.com",
        password="retryability password",
    )
    with Session(SYNC_ENGINE, expire_on_commit=False) as db:
        profile = AITaskProfile(
            tenant_id=tenant_id,
            task_type=AITaskType.CUSTOMER_RESPONSE.value,
            timeout_seconds=5,
            attempts_per_route=1,
        )
        db.add(profile)
        db.flush()
        db.add_all(
            [
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="retryable-mock",
                    model_id="retryable-model",
                    priority=0,
                    parameters={},
                ),
                AITaskRoute(
                    tenant_id=tenant_id,
                    profile_id=profile.id,
                    provider="missing-provider",
                    model_id="missing-model",
                    priority=1,
                    parameters={},
                ),
            ]
        )
        db.commit()

    retryable = MockAIProviderAdapter(key="retryable-mock", failures_before_success=1)
    async_engine, session_factory = create_database(TEST_SETTINGS)
    gateway = AIGateway(AIProviderRegistry([retryable]), session_factory)

    async def run() -> None:
        try:
            with pytest.raises(AIRoutingError) as raised:
                await gateway.generate(
                    tenant_id,
                    AITaskType.CUSTOMER_RESPONSE,
                    GenerationRequest(input_text="Please retry later"),
                )
            assert raised.value.code == "provider_unavailable"
            assert raised.value.retryable is True
        finally:
            await async_engine.dispose()

    asyncio.run(run())
'''
if "def test_retryable_failure_is_not_masked_by_later_unavailable_route()" not in content:
    content += new_ai_test
ai_test.write_text(content)


agent_test = Path("apps/api/tests/test_agent_prompt_integration.py")
content = agent_test.read_text()
old_assertions = '''        history_payload = history_section.split("\\n", 1)[1]
        assert len(history_payload) <= 10
        assert current_section == "CUSTOMER: Please help with my order"
'''
new_assertions = '''        history_payload = history_section.split("\\n", 1)[1]
        assert len(history_payload) <= 10
        assert current_section == "CUSTOMER: Please help with my order"

        async def empty_budget_context() -> str:
            engine, session_factory = create_database(TEST_SETTINGS)
            try:
                return await build_conversation_input(
                    session_factory,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    current_message_id=inbound_message_id,
                    current_text="Please help with my order",
                    history_char_budget=0,
                )
            finally:
                await engine.dispose()

        zero_budget_input = asyncio.run(empty_budget_context())
        zero_history = zero_budget_input.split("\\n\\nCurrent customer message:\\n", 1)[0]
        assert zero_history == "Conversation history (oldest to newest):\\n"
'''
if old_assertions not in content:
    raise SystemExit("context budget assertions not found")
content = content.replace(old_assertions, new_assertions, 1)
agent_test.write_text(content)
