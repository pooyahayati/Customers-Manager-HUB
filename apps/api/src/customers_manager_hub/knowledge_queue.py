from dataclasses import dataclass
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError

KNOWLEDGE_JOB_STREAM = "cmh:jobs:knowledge-ingestion:v1"
KNOWLEDGE_JOB_GROUP = "cmh:knowledge-workers:v1"
KNOWLEDGE_JOB_TYPE = "knowledge.ingest"
KNOWLEDGE_JOB_CLAIM_IDLE_MS = 60_000
KNOWLEDGE_JOB_DEAD_LETTER_STREAM = "cmh:jobs:knowledge-ingestion:dead-letter:v1"
KNOWLEDGE_JOB_ATTEMPT_HASH = "cmh:jobs:knowledge-ingestion:attempts:v1"


@dataclass(frozen=True, slots=True)
class KnowledgeJob:
    stream_id: str
    source_id: UUID
    attempt: int = 1


class KnowledgeJobQueue:
    def __init__(
        self,
        redis_client: Redis,
        *,
        claim_idle_ms: int = KNOWLEDGE_JOB_CLAIM_IDLE_MS,
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
                KNOWLEDGE_JOB_STREAM,
                KNOWLEDGE_JOB_GROUP,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def enqueue(self, source_id: UUID) -> str:
        stream_id = str(
            await self._redis.xadd(
                KNOWLEDGE_JOB_STREAM,
                {"job_type": KNOWLEDGE_JOB_TYPE, "source_id": str(source_id)},
            )
        )
        await self._redis.hset(KNOWLEDGE_JOB_ATTEMPT_HASH, stream_id, 1)
        return stream_id

    async def consume(
        self,
        consumer_name: str,
        *,
        count: int = 5,
        block_ms: int = 1,
    ) -> list[KnowledgeJob]:
        raw = await self._redis.xreadgroup(
            KNOWLEDGE_JOB_GROUP,
            consumer_name,
            {KNOWLEDGE_JOB_STREAM: ">"},
            count=count,
            block=block_ms,
        )
        reply = cast(list[tuple[str, list[tuple[str, dict[str, str]]]]], raw)
        jobs: list[KnowledgeJob] = []
        for stream_name, entries in reply:
            if stream_name == KNOWLEDGE_JOB_STREAM:
                jobs.extend(await self._decode_entries(entries, reclaimed=False))
        return jobs

    async def reclaim(
        self,
        consumer_name: str,
        *,
        count: int = 5,
    ) -> list[KnowledgeJob]:
        raw = await self._redis.xautoclaim(
            KNOWLEDGE_JOB_STREAM,
            KNOWLEDGE_JOB_GROUP,
            consumer_name,
            min_idle_time=self._claim_idle_ms,
            start_id="0-0",
            count=count,
        )
        reply = cast(tuple[str, list[tuple[str, dict[str, str]]], list[str]], raw)
        _, entries, _ = reply
        return await self._decode_entries(entries, reclaimed=True)

    async def acknowledge(self, stream_id: str) -> None:
        await self._redis.xack(KNOWLEDGE_JOB_STREAM, KNOWLEDGE_JOB_GROUP, stream_id)
        await self._redis.xdel(KNOWLEDGE_JOB_STREAM, stream_id)
        await self._redis.hdel(KNOWLEDGE_JOB_ATTEMPT_HASH, stream_id)

    async def _attempt(self, stream_id: str, *, reclaimed: bool) -> int:
        await self._redis.hsetnx(KNOWLEDGE_JOB_ATTEMPT_HASH, stream_id, 1)
        if reclaimed:
            return int(await self._redis.hincrby(KNOWLEDGE_JOB_ATTEMPT_HASH, stream_id, 1))
        raw = await self._redis.hget(KNOWLEDGE_JOB_ATTEMPT_HASH, stream_id)
        try:
            return max(int(cast(str | int | None, raw) or 1), 1)
        except TypeError, ValueError:
            await self._redis.hset(KNOWLEDGE_JOB_ATTEMPT_HASH, stream_id, 1)
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
            KNOWLEDGE_JOB_DEAD_LETTER_STREAM,
            {
                "job_type": KNOWLEDGE_JOB_TYPE,
                "source_id": entity_id[:64],
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
    ) -> list[KnowledgeJob]:
        jobs: list[KnowledgeJob] = []
        for stream_id, fields in entries:
            attempt = await self._attempt(stream_id, reclaimed=reclaimed)
            raw_source_id = fields.get("source_id", "")
            if fields.get("job_type") != KNOWLEDGE_JOB_TYPE:
                await self._dead_letter(
                    stream_id,
                    entity_id=raw_source_id,
                    attempt=attempt,
                    reason_code="malformed_job_type",
                )
                continue
            try:
                source_id = UUID(raw_source_id)
            except ValueError:
                await self._dead_letter(
                    stream_id,
                    entity_id=raw_source_id,
                    attempt=attempt,
                    reason_code="malformed_source_id",
                )
                continue
            if attempt > self._max_delivery_attempts:
                await self._dead_letter(
                    stream_id,
                    entity_id=str(source_id),
                    attempt=attempt,
                    reason_code="delivery_attempts_exhausted",
                )
                continue
            jobs.append(KnowledgeJob(stream_id=stream_id, source_id=source_id, attempt=attempt))
        return jobs
