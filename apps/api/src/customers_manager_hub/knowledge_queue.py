from dataclasses import dataclass
from typing import cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError

KNOWLEDGE_JOB_STREAM = "cmh:jobs:knowledge-ingestion:v1"
KNOWLEDGE_JOB_GROUP = "cmh:knowledge-workers:v1"
KNOWLEDGE_JOB_TYPE = "knowledge.ingest"
KNOWLEDGE_JOB_CLAIM_IDLE_MS = 60_000


@dataclass(frozen=True, slots=True)
class KnowledgeJob:
    stream_id: str
    source_id: UUID


class KnowledgeJobQueue:
    def __init__(
        self,
        redis_client: Redis,
        *,
        claim_idle_ms: int = KNOWLEDGE_JOB_CLAIM_IDLE_MS,
    ) -> None:
        if claim_idle_ms < 0:
            raise ValueError("claim_idle_ms must not be negative")
        self._redis = redis_client
        self._claim_idle_ms = claim_idle_ms

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
        stream_id = await self._redis.xadd(
            KNOWLEDGE_JOB_STREAM,
            {"job_type": KNOWLEDGE_JOB_TYPE, "source_id": str(source_id)},
        )
        return str(stream_id)

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
        return self._parse_reply(reply)

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
        return self._parse_entries(entries)

    async def acknowledge(self, stream_id: str) -> None:
        await self._redis.xack(KNOWLEDGE_JOB_STREAM, KNOWLEDGE_JOB_GROUP, stream_id)
        await self._redis.xdel(KNOWLEDGE_JOB_STREAM, stream_id)

    @staticmethod
    def _parse_reply(
        reply: list[tuple[str, list[tuple[str, dict[str, str]]]]],
    ) -> list[KnowledgeJob]:
        jobs: list[KnowledgeJob] = []
        for stream_name, entries in reply:
            if stream_name != KNOWLEDGE_JOB_STREAM:
                continue
            jobs.extend(KnowledgeJobQueue._parse_entries(entries))
        return jobs

    @staticmethod
    def _parse_entries(entries: list[tuple[str, dict[str, str]]]) -> list[KnowledgeJob]:
        jobs: list[KnowledgeJob] = []
        for stream_id, fields in entries:
            if fields.get("job_type") != KNOWLEDGE_JOB_TYPE:
                continue
            raw_source_id = fields.get("source_id")
            if raw_source_id is None:
                continue
            try:
                source_id = UUID(raw_source_id)
            except ValueError:
                continue
            jobs.append(KnowledgeJob(stream_id=stream_id, source_id=source_id))
        return jobs
