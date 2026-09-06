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


@dataclass(frozen=True, slots=True)
class ChannelJob:
    stream_id: str
    event_id: UUID


def create_channel_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url, decode_responses=True)


class ChannelJobQueue:
    def __init__(
        self,
        redis_client: Redis,
        *,
        claim_idle_ms: int = CHANNEL_JOB_CLAIM_IDLE_MS,
    ) -> None:
        if claim_idle_ms < 0:
            raise ValueError("claim_idle_ms must not be negative")
        self._redis = redis_client
        self._claim_idle_ms = claim_idle_ms

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
        stream_id = await self._redis.xadd(
            CHANNEL_JOB_STREAM,
            {"job_type": CHANNEL_JOB_TYPE, "event_id": str(event_id)},
        )
        return str(stream_id)

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
        return self._parse_reply(reply)

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
        return self._parse_entries(entries)

    async def acknowledge(self, stream_id: str) -> None:
        await self._redis.xack(CHANNEL_JOB_STREAM, CHANNEL_JOB_GROUP, stream_id)
        await self._redis.xdel(CHANNEL_JOB_STREAM, stream_id)

    @staticmethod
    def _parse_reply(
        reply: list[tuple[str, list[tuple[str, dict[str, str]]]]],
    ) -> list[ChannelJob]:
        jobs: list[ChannelJob] = []
        for stream_name, entries in reply:
            if stream_name != CHANNEL_JOB_STREAM:
                continue
            jobs.extend(ChannelJobQueue._parse_entries(entries))
        return jobs

    @staticmethod
    def _parse_entries(entries: list[tuple[str, dict[str, str]]]) -> list[ChannelJob]:
        jobs: list[ChannelJob] = []
        for stream_id, fields in entries:
            if fields.get("job_type") != CHANNEL_JOB_TYPE:
                continue
            raw_event_id = fields.get("event_id")
            if raw_event_id is None:
                continue
            try:
                event_id = UUID(raw_event_id)
            except ValueError:
                continue
            jobs.append(ChannelJob(stream_id=stream_id, event_id=event_id))
        return jobs
