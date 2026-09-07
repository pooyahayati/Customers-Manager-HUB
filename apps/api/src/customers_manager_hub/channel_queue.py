from dataclasses import dataclass
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from customers_manager_hub.config import Settings

CHANNEL_JOB_STREAM = "cmh:jobs:channel-inbound:v1"
CHANNEL_JOB_GROUP = "cmh:channel-workers:v1"
CHANNEL_JOB_TYPE = "channel.inbound"
CHANNEL_JOB_CLAIM_IDLE_MS = 30_000
CHANNEL_JOB_DEAD_LETTER_STREAM = "cmh:jobs:channel-inbound:dead-letter:v1"
CHANNEL_JOB_ATTEMPT_HASH = "cmh:jobs:channel-inbound:attempts:v1"


@dataclass(frozen=True, slots=True)
class ChannelJob:
    stream_id: str
    event_id: UUID
    attempt: int = 1


def create_channel_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)  # pyright: ignore[reportUnknownMemberType]


class ChannelJobQueue:
    def __init__(
        self,
        redis_client: Redis,
        *,
        claim_idle_ms: int = CHANNEL_JOB_CLAIM_IDLE_MS,
        max_delivery_attempts: int = 5,
    ) -> None:
        if claim_idle_ms < 0:
            raise ValueError("claim_idle_ms must not be negative")
        if max_delivery_attempts < 1:
            raise ValueError("max_delivery_attempts must be positive")
        self._redis = redis_client
        self._claim_idle_ms = claim_idle_ms
        self._max_delivery_attempts = max_delivery_attempts

    async def ensure_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                CHANNEL_JOB_STREAM,
                CHANNEL_JOB_GROUP,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, event_id: UUID) -> str:
        stream_id = str(
            await self._redis.xadd(
                CHANNEL_JOB_STREAM,
                {"job_type": CHANNEL_JOB_TYPE, "event_id": str(event_id)},
            )
        )
        await self._redis.hset(CHANNEL_JOB_ATTEMPT_HASH, stream_id, 1)
        return stream_id

    async def consume(
        self,
        consumer_name: str,
        *,
        count: int = 10,
        block_ms: int = 1_000,
    ) -> list[ChannelJob]:
        raw = await self._redis.xreadgroup(
            CHANNEL_JOB_GROUP,
            consumer_name,
            {CHANNEL_JOB_STREAM: ">"},
            count=count,
            block=block_ms,
        )
        reply = cast(list[tuple[str, list[tuple[str, dict[str, str]]]]], raw)
        jobs: list[ChannelJob] = []
        for stream_name, entries in reply:
            if stream_name == CHANNEL_JOB_STREAM:
                jobs.extend(await self._decode_entries(entries, reclaimed=False))
        return jobs

    async def reclaim(
        self,
        consumer_name: str,
        *,
        count: int = 10,
    ) -> list[ChannelJob]:
        raw = await self._redis.xautoclaim(
            CHANNEL_JOB_STREAM,
            CHANNEL_JOB_GROUP,
            consumer_name,
            min_idle_time=self._claim_idle_ms,
            start_id="0-0",
            count=count,
        )
        reply = cast(tuple[str, list[tuple[str, dict[str, str]]], list[str]], raw)
        _, entries, _ = reply
        return await self._decode_entries(entries, reclaimed=True)

    async def acknowledge(self, stream_id: str) -> None:
        await self._redis.xack(CHANNEL_JOB_STREAM, CHANNEL_JOB_GROUP, stream_id)
        await self._redis.xdel(CHANNEL_JOB_STREAM, stream_id)
        await self._redis.hdel(CHANNEL_JOB_ATTEMPT_HASH, stream_id)

    async def _attempt(self, stream_id: str, *, reclaimed: bool) -> int:
        await self._redis.hsetnx(CHANNEL_JOB_ATTEMPT_HASH, stream_id, 1)
        if reclaimed:
            return int(await self._redis.hincrby(CHANNEL_JOB_ATTEMPT_HASH, stream_id, 1))
        raw = await self._redis.hget(CHANNEL_JOB_ATTEMPT_HASH, stream_id)
        try:
            return max(int(cast(str | int | None, raw) or 1), 1)
        except TypeError, ValueError:
            await self._redis.hset(CHANNEL_JOB_ATTEMPT_HASH, stream_id, 1)
            return 1

    async def _dead_letter(
        self,
        stream_id: str,
        *,
        entity_id: str,
        attempt: int,
        reason_code: str,
    ) -> None:
        await self._redis.xadd(
            CHANNEL_JOB_DEAD_LETTER_STREAM,
            {
                "job_type": CHANNEL_JOB_TYPE,
                "event_id": entity_id[:64],
                "source_stream_id": stream_id[:64],
                "attempts": str(max(attempt, 1)),
                "reason_code": reason_code[:100],
            },
        )
        await self.acknowledge(stream_id)

    async def _decode_entries(
        self,
        entries: list[tuple[str, dict[str, str]]],
        *,
        reclaimed: bool,
    ) -> list[ChannelJob]:
        jobs: list[ChannelJob] = []
        for stream_id, fields in entries:
            attempt = await self._attempt(stream_id, reclaimed=reclaimed)
            raw_event_id = fields.get("event_id", "")
            if fields.get("job_type") != CHANNEL_JOB_TYPE:
                await self._dead_letter(
                    stream_id,
                    entity_id=raw_event_id,
                    attempt=attempt,
                    reason_code="malformed_job_type",
                )
                continue
            try:
                event_id = UUID(raw_event_id)
            except ValueError:
                await self._dead_letter(
                    stream_id,
                    entity_id=raw_event_id,
                    attempt=attempt,
                    reason_code="malformed_event_id",
                )
                continue
            if attempt > self._max_delivery_attempts:
                await self._dead_letter(
                    stream_id,
                    entity_id=str(event_id),
                    attempt=attempt,
                    reason_code="delivery_attempts_exhausted",
                )
                continue
            jobs.append(ChannelJob(stream_id=stream_id, event_id=event_id, attempt=attempt))
        return jobs
