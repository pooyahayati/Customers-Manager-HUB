from pathlib import Path


ai_gateway = Path("apps/api/src/customers_manager_hub/ai_gateway.py")
content = ai_gateway.read_text()
old = '''class AIRoutingError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
'''
new = '''class AIRoutingError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
'''
if old not in content:
    raise SystemExit("AIRoutingError block not found")
content = content.replace(old, new, 1)

old = '''        profile = await self._load_profile(tenant_id, task_type)
        last_error_code = "all_routes_failed"

        for route in profile.routes:
'''
new = '''        profile = await self._load_profile(tenant_id, task_type)
        last_error_code = "all_routes_failed"
        last_error_retryable = False

        for route in profile.routes:
'''
if old not in content:
    raise SystemExit("execute initialization block not found")
content = content.replace(old, new, 1)

content = content.replace(
    '''            if adapter is None:
                last_error_code = "provider_unavailable"
''',
    '''            if adapter is None:
                last_error_code = "provider_unavailable"
                last_error_retryable = False
''',
    1,
)
content = content.replace(
    '''            if not adapter.supports(operation):
                last_error_code = "provider_capability_unavailable"
''',
    '''            if not adapter.supports(operation):
                last_error_code = "provider_capability_unavailable"
                last_error_retryable = False
''',
    1,
)
content = content.replace(
    '''                    last_error_code = exc.code
                    await self._record_trace(
''',
    '''                    last_error_code = exc.code
                    last_error_retryable = exc.retryable
                    await self._record_trace(
''',
    1,
)
old = '''        raise AIRoutingError(last_error_code)
'''
new = '''        raise AIRoutingError(last_error_code, retryable=last_error_retryable)
'''
if old not in content:
    raise SystemExit("AIRoutingError raise not found")
content = content.replace(old, new, 1)
ai_gateway.write_text(content)


test_path = Path("apps/api/tests/test_agent_prompt_integration.py")
content = test_path.read_text()
content = content.replace(
    "from collections.abc import Iterator\nfrom uuid import UUID\n",
    "from collections.abc import Iterator\nfrom datetime import UTC, datetime\nfrom uuid import UUID\n",
    1,
)
content = content.replace(
    '            occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),',
    "            occurred_at=datetime.now(UTC),",
    1,
)
old = '''        with Session(SYNC_ENGINE) as db:
            failed_run = db.scalar(
                select(AgentRun).where(AgentRun.inbound_message_id == Message.id)
            )
            runs = list(db.scalars(select(AgentRun)).all())
            assert len(runs) == 1
            assert runs[0].status == AgentRunStatus.FAILED.value
            assert runs[0].error_code == "agent_empty_output"
            assert runs[0].generated_text is None
            del failed_run
'''
new = '''        with Session(SYNC_ENGINE) as db:
            runs = list(db.scalars(select(AgentRun)).all())
            assert len(runs) == 1
            assert runs[0].status == AgentRunStatus.FAILED.value
            assert runs[0].error_code == "agent_empty_output"
            assert runs[0].generated_text is None
'''
if old not in content:
    raise SystemExit("failed-run assertion block not found")
content = content.replace(old, new, 1)
test_path.write_text(content)
