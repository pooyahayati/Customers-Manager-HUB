from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text()
    if old not in text:
        raise RuntimeError(f"patch anchor not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1))


replace_once(
    "apps/api/src/customers_manager_hub/channels.py",
    "    required_credentials = (\n",
    "    required_credentials: set[ChannelCredentialKind] = (\n",
)

replace_once(
    "apps/api/src/customers_manager_hub/website_chat.py",
    "    result = {account_id: [] for account_id in account_ids}\n",
    "    result: dict[UUID, list[str]] = {account_id: [] for account_id in account_ids}\n",
)

replace_once(
    "apps/api/src/customers_manager_hub/website_chat.py",
    '''                .order_by(Message.occurred_at, Message.created_at, Message.id)\n                .limit(_MAX_PUBLIC_MESSAGES)\n            )\n        ).all()\n    )\n    return [\n''',
    '''                .order_by(\n                    Message.occurred_at.desc(),\n                    Message.created_at.desc(),\n                    Message.id.desc(),\n                )\n                .limit(_MAX_PUBLIC_MESSAGES)\n            )\n        ).all()\n    )\n    rows.reverse()\n    return [\n''',
)

replace_once(
    "apps/api/tests/test_website_chat_integration.py",
    "    app, client = open_client()\n",
    "    _, client = open_client()\n",
)

print("Milestone 7 Website Chat follow-up patch applied")
