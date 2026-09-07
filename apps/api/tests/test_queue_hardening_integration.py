import asyncio
import os
from contextlib import suppress
from typing import cast
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from customers_manager_hub.channel_queue import (
    CHANNEL_JOB_ATTEMPT_HASH,
    CHANNEL_JOB_DEAD_LETTER_STREAM,
    CHANNEL_JOB_GROUP,
    CHANNEL_JOB_STREAM,
    CHANNEL_JOB_TYPE,
    ChannelJobQueue,
)
from customers_manager_hub.knowledge_queue import (
    KNOWLEDGE_JOB_ATTEMPT_HASH,
    KNOWLEDGE_JOB_DEAD_LETTER_STREAM,
    KNOWLEDGE_JOB_GROUP,
    KNOWLEDGE_JOB_STREAM,
    KnowledgeJobQueue,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="integration test requires the CI Redis service",
)


def _redis() -> Redis:
    return Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        decode_responses=True,
    )


def test_channel_retry_budget_moves_exhausted_and_malformed_jobs_to_dlq() -> None:
    async def scenario() -> None:
        redis = _redis()
        try:
            await redis.delete(
                CHANNEL_JOB_STREAM,
                CHANNEL_JOB_DEAD_LETTER_STREAM,
                CHANNEL_JOB_ATTEMPT_HASH,
            )
            queue = ChannelJobQueue(redis, claim_idle_ms=0, max_delivery_attempts=2)
            await queue.ensure_group()

            event_id = uuid4()
            await queue.enqueue(event_id)
            initial = await queue.consume("channel-test-1", block_ms=1)
            assert len(initial) == 1
            assert initial[0].event_id == event_id
            assert initial[0].attempt == 1

            retry = await queue.reclaim("channel-test-2")
            assert len(retry) == 1
            assert retry[0].attempt == 2

            exhausted = await queue.reclaim("channel-test-3")
            assert exhausted == []
            dead_letters = cast(
                list[tuple[str, dict[str, str]]],
                await redis.xrange(CHANNEL_JOB_DEAD_LETTER_STREAM),
            )
            assert len(dead_letters) == 1
            assert dead_letters[0][1]["event_id"] == str(event_id)
            assert dead_letters[0][1]["attempts"] == "3"
            assert dead_letters[0][1]["reason_code"] == "delivery_attempts_exhausted"
            assert await redis.xlen(CHANNEL_JOB_STREAM) == 0

            malformed_stream_id = str(
                await redis.xadd(
                    CHANNEL_JOB_STREAM,
                    {"job_type": CHANNEL_JOB_TYPE, "event_id": "not-a-uuid"},
                )
            )
            malformed = await queue.consume("channel-test-4", block_ms=1)
            assert malformed == []
            assert await redis.xlen(CHANNEL_JOB_STREAM) == 0
            assert await redis.hget(CHANNEL_JOB_ATTEMPT_HASH, malformed_stream_id) is None

            dead_letters = cast(
                list[tuple[str, dict[str, str]]],
                await redis.xrange(CHANNEL_JOB_DEAD_LETTER_STREAM),
            )
            assert len(dead_letters) == 2
            assert dead_letters[-1][1]["reason_code"] == "malformed_event_id"
        finally:
            await redis.delete(
                CHANNEL_JOB_STREAM,
                CHANNEL_JOB_DEAD_LETTER_STREAM,
                CHANNEL_JOB_ATTEMPT_HASH,
            )
            with suppress(Exception):
                await redis.xgroup_destroy(CHANNEL_JOB_STREAM, CHANNEL_JOB_GROUP)
            await redis.aclose()

    asyncio.run(scenario())


def test_knowledge_retry_budget_moves_exhausted_job_to_dlq() -> None:
    async def scenario() -> None:
        redis = _redis()
        try:
            await redis.delete(
                KNOWLEDGE_JOB_STREAM,
                KNOWLEDGE_JOB_DEAD_LETTER_STREAM,
                KNOWLEDGE_JOB_ATTEMPT_HASH,
            )
            queue = KnowledgeJobQueue(redis, claim_idle_ms=0, max_delivery_attempts=1)
            await queue.ensure_group()

            source_id = uuid4()
            await queue.enqueue(source_id)
            initial = await queue.consume("knowledge-test-1", block_ms=1)
            assert len(initial) == 1
            assert initial[0].source_id == source_id
            assert initial[0].attempt == 1

            exhausted = await queue.reclaim("knowledge-test-2")
            assert exhausted == []
            dead_letters = cast(
                list[tuple[str, dict[str, str]]],
                await redis.xrange(KNOWLEDGE_JOB_DEAD_LETTER_STREAM),
            )
            assert len(dead_letters) == 1
            assert dead_letters[0][1]["source_id"] == str(source_id)
            assert dead_letters[0][1]["attempts"] == "2"
            assert dead_letters[0][1]["reason_code"] == "delivery_attempts_exhausted"
            assert await redis.xlen(KNOWLEDGE_JOB_STREAM) == 0
        finally:
            await redis.delete(
                KNOWLEDGE_JOB_STREAM,
                KNOWLEDGE_JOB_DEAD_LETTER_STREAM,
                KNOWLEDGE_JOB_ATTEMPT_HASH,
            )
            with suppress(Exception):
                await redis.xgroup_destroy(KNOWLEDGE_JOB_STREAM, KNOWLEDGE_JOB_GROUP)
            await redis.aclose()

    asyncio.run(scenario())
