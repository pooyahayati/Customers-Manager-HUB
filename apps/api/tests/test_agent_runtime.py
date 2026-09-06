import pytest

from customers_manager_hub.agent_runtime import (
    AgentRuntimeError,
    channel_instruction,
    compose_instructions,
    validate_customer_response,
)
from customers_manager_hub.channel_models import ChannelType


def test_prompt_composer_keeps_tenant_prompt_in_instruction_layer() -> None:
    tenant_prompt = "Answer in Persian and keep responses practical."
    instructions = compose_instructions(tenant_prompt, ChannelType.TELEGRAM)

    assert "[PLATFORM RUNTIME POLICY]" in instructions
    assert "[TENANT PUBLISHED PROMPT]" in instructions
    assert tenant_prompt in instructions
    assert "[CHANNEL INSTRUCTIONS]" in instructions
    assert "Telegram private chat" in instructions
    assert "4096" in instructions


def test_telegram_channel_instruction_is_deterministic() -> None:
    first = channel_instruction(ChannelType.TELEGRAM)
    second = channel_instruction(ChannelType.TELEGRAM)
    assert first == second
    assert "plain text" in first


def test_customer_response_validation_normalizes_valid_text() -> None:
    assert validate_customer_response("  hello customer  ") == "hello customer"


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        ("   ", "agent_empty_output"),
        ("hello\x00world", "agent_nul_output"),
        ("x" * 4097, "agent_output_too_long"),
    ],
)
def test_customer_response_validation_rejects_invalid_output(
    value: str,
    expected_code: str,
) -> None:
    with pytest.raises(AgentRuntimeError) as captured:
        validate_customer_response(value)
    assert captured.value.code == expected_code
    assert captured.value.retryable is False
