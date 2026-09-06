from pathlib import Path

path = Path("apps/api/tests/test_telegram_channel_integration.py")
content = path.read_text()

marker = '''    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer-a@example.com",
        password="viewer password tenant a",
        role=TenantRole.VIEWER,
        tenant_id=tenant_a,
    )
'''
replacement = '''    seed_tenant_user(
        tenant_slug="unused-admin",
        tenant_name="unused",
        email="admin-a@example.com",
        password="admin password tenant a",
        role=TenantRole.ADMIN,
        tenant_id=tenant_a,
    )
    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer-a@example.com",
        password="viewer password tenant a",
        role=TenantRole.VIEWER,
        tenant_id=tenant_a,
    )
'''
if marker not in content:
    raise SystemExit("viewer seed marker not found")
content = content.replace(marker, replacement, 1)

marker = '''    _, viewer_client = channel_client(adapter)
    try:
        login(viewer_client, "viewer-a@example.com", "viewer password tenant a")
'''
replacement = '''    _, admin_client = channel_client(adapter)
    try:
        login(admin_client, "admin-a@example.com", "admin password tenant a")
        allowed = admin_client.patch(
            f"/api/v1/tenants/{tenant_a}/channels/{account_id}",
            json={"name": "Admin Updated Bot"},
        )
        assert allowed.status_code == 200
        assert allowed.json()["name"] == "Admin Updated Bot"
    finally:
        close_channel_client(admin_client)

    _, viewer_client = channel_client(adapter)
    try:
        login(viewer_client, "viewer-a@example.com", "viewer password tenant a")
'''
if marker not in content:
    raise SystemExit("viewer client marker not found")
content = content.replace(marker, replacement, 1)

marker = '''        assert (
            webhook(client, account_id, "wrong-secret", telegram_text_update(100, 10)).status_code
            == 401
        )
        accepted = webhook(client, account_id, secret, telegram_text_update(100, 10))
'''
replacement = '''        missing_secret = client.post(
            f"/api/v1/webhooks/telegram/{account_id}",
            json=telegram_text_update(100, 10),
        )
        assert missing_secret.status_code == 401
        assert (
            webhook(client, account_id, "wrong-secret", telegram_text_update(100, 10)).status_code
            == 401
        )
        accepted = webhook(client, account_id, secret, telegram_text_update(100, 10))
'''
if marker not in content:
    raise SystemExit("webhook secret marker not found")
content = content.replace(marker, replacement, 1)

marker = '''        initial_stream_length = SYNC_REDIS.xlen(CHANNEL_JOB_STREAM)
        assert initial_stream_length == 1
        duplicate = webhook(client, account_id, secret, telegram_text_update(100, 10))
'''
replacement = '''        initial_stream_length = SYNC_REDIS.xlen(CHANNEL_JOB_STREAM)
        assert initial_stream_length == 1
        stream_payload = repr(SYNC_REDIS.xrange(CHANNEL_JOB_STREAM))
        assert "test-bot-token" not in stream_payload
        assert secret not in stream_payload
        assert "job_type" in stream_payload
        assert str(event_id) in stream_payload
        duplicate = webhook(client, account_id, secret, telegram_text_update(100, 10))
'''
if marker not in content:
    raise SystemExit("stream marker not found")
content = content.replace(marker, replacement, 1)

marker = '''    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer@example.com",
        password="viewer password",
        role=TenantRole.VIEWER,
        tenant_id=tenant_id,
    )
    adapter = FakeTelegramAdapter()
'''
replacement = '''    seed_tenant_user(
        tenant_slug="unused-agent",
        tenant_name="unused",
        email="agent@example.com",
        password="agent password",
        role=TenantRole.AGENT,
        tenant_id=tenant_id,
    )
    seed_tenant_user(
        tenant_slug="unused-viewer",
        tenant_name="unused",
        email="viewer@example.com",
        password="viewer password",
        role=TenantRole.VIEWER,
        tenant_id=tenant_id,
    )
    adapter = FakeTelegramAdapter()
'''
if marker not in content:
    raise SystemExit("outbound viewer seed marker not found")
content = content.replace(marker, replacement, 1)

marker = '''        login(supervisor_client, "supervisor@example.com", "supervisor password")
        payload = {
'''
replacement = '''        login(supervisor_client, "supervisor@example.com", "supervisor password")
        denied_config = supervisor_client.patch(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}",
            json={"name": "Supervisor Cannot Change This"},
        )
        assert denied_config.status_code == 403
        payload = {
'''
if marker not in content:
    raise SystemExit("supervisor login marker not found")
content = content.replace(marker, replacement, 1)

marker = '''    _, viewer_client = channel_client(adapter)
    try:
        login(viewer_client, "viewer@example.com", "viewer password")
'''
replacement = '''    _, agent_client = channel_client(adapter)
    try:
        login(agent_client, "agent@example.com", "agent password")
        denied_config = agent_client.patch(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}",
            json={"name": "Agent Cannot Change This"},
        )
        assert denied_config.status_code == 403
        agent_sent = agent_client.post(
            f"/api/v1/tenants/{tenant_id}/channels/{account_id}/send-text",
            json={
                "conversation_id": str(conversation_id),
                "text": "Agent reply",
                "idempotency_key": "telegram-outbound:agent",
            },
        )
        assert agent_sent.status_code == 200
        assert agent_sent.json()["direction"] == MessageDirection.OUTBOUND.value
    finally:
        close_channel_client(agent_client)

    _, viewer_client = channel_client(adapter)
    try:
        login(viewer_client, "viewer@example.com", "viewer password")
'''
if marker not in content:
    raise SystemExit("outbound viewer client marker not found")
content = content.replace(marker, replacement, 1)

path.write_text(content)
