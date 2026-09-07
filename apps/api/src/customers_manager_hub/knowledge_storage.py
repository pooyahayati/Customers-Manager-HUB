import asyncio
from collections.abc import Mapping
from typing import Protocol, cast
from uuid import UUID

import boto3  # pyright: ignore[reportMissingTypeStubs]
from botocore.config import Config as BotocoreConfig  # pyright: ignore[reportMissingTypeStubs]
from botocore.exceptions import (  # pyright: ignore[reportMissingTypeStubs]
    BotoCoreError,
    ClientError,
)

from customers_manager_hub.config import Settings

PDF_MEDIA_TYPE = "application/pdf"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_KNOWLEDGE_MEDIA_TYPES = frozenset({PDF_MEDIA_TYPE, XLSX_MEDIA_TYPE})
MAX_KNOWLEDGE_OBJECT_BYTES = 20 * 1024 * 1024


class KnowledgeStorageError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class ObjectStorage(Protocol):
    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None: ...

    async def get_bytes(self, key: str) -> bytes: ...

    async def delete_object(self, key: str) -> None: ...


class _StreamingBody(Protocol):
    def read(self, amt: int | None = None) -> bytes: ...

    def close(self) -> None: ...


class _S3Client(Protocol):
    def put_object(self, **kwargs: object) -> object: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def delete_object(self, **kwargs: object) -> object: ...


class S3ObjectStorage:
    def __init__(self, settings: Settings) -> None:
        if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
            raise ValueError("S3 credentials must be configured")
        client = boto3.client(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
            config=BotocoreConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                retries={"max_attempts": 2, "mode": "standard"},
                connect_timeout=settings.dependency_timeout_seconds,
                read_timeout=max(settings.dependency_timeout_seconds, 10),
            ),
        )
        self._client = cast(_S3Client, client)
        self._bucket = settings.s3_bucket

    @staticmethod
    def _client_error(exc: ClientError) -> KnowledgeStorageError:
        response = cast(dict[str, object], exc.response)
        metadata = response.get("ResponseMetadata")
        status_code = None
        if isinstance(metadata, dict):
            status_value = cast(dict[object, object], metadata).get("HTTPStatusCode")
            if isinstance(status_value, int):
                status_code = status_value
        retryable = status_code is None or status_code in {408, 409, 425, 429} or status_code >= 500
        return KnowledgeStorageError("knowledge_storage_request_failed", retryable=retryable)

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError("Invalid object key")
        if media_type not in SUPPORTED_KNOWLEDGE_MEDIA_TYPES:
            raise ValueError("Unsupported knowledge media type")
        if len(data) > MAX_KNOWLEDGE_OBJECT_BYTES:
            raise ValueError("Knowledge object exceeds maximum size")

        def _put() -> None:
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=data,
                    ContentType=media_type,
                )
            except ClientError as exc:
                raise self._client_error(exc) from exc
            except BotoCoreError as exc:
                raise KnowledgeStorageError(
                    "knowledge_storage_transport_error", retryable=True
                ) from exc

        await asyncio.to_thread(_put)

    async def get_bytes(self, key: str) -> bytes:
        def _get() -> bytes:
            body: _StreamingBody | None = None
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
                raw_body = response.get("Body")
                if raw_body is None:
                    raise KnowledgeStorageError(
                        "knowledge_storage_invalid_response", retryable=True
                    )
                body = cast(_StreamingBody, raw_body)
                payload = body.read(MAX_KNOWLEDGE_OBJECT_BYTES + 1)
                if len(payload) > MAX_KNOWLEDGE_OBJECT_BYTES:
                    raise KnowledgeStorageError(
                        "knowledge_storage_object_too_large", retryable=False
                    )
                return payload
            except ClientError as exc:
                raise self._client_error(exc) from exc
            except BotoCoreError as exc:
                raise KnowledgeStorageError(
                    "knowledge_storage_transport_error", retryable=True
                ) from exc
            finally:
                if body is not None:
                    body.close()

        return await asyncio.to_thread(_get)

    async def delete_object(self, key: str) -> None:
        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                raise self._client_error(exc) from exc
            except BotoCoreError as exc:
                raise KnowledgeStorageError(
                    "knowledge_storage_transport_error", retryable=True
                ) from exc

        await asyncio.to_thread(_delete)


class UnavailableObjectStorage:
    @staticmethod
    def _error() -> KnowledgeStorageError:
        return KnowledgeStorageError("knowledge_storage_not_configured", retryable=False)

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        del key, data, media_type
        raise self._error()

    async def get_bytes(self, key: str) -> bytes:
        del key
        raise self._error()

    async def delete_object(self, key: str) -> None:
        del key
        raise self._error()


class InMemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        if len(data) > MAX_KNOWLEDGE_OBJECT_BYTES:
            raise ValueError("Knowledge object exceeds maximum size")
        self.objects[key] = (bytes(data), media_type)

    async def get_bytes(self, key: str) -> bytes:
        item = self.objects.get(key)
        if item is None:
            raise KnowledgeStorageError("knowledge_storage_not_found", retryable=False)
        return item[0]

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)


def build_object_storage(settings: Settings) -> ObjectStorage:
    if settings.s3_access_key_id is None or settings.s3_secret_access_key is None:
        return UnavailableObjectStorage()
    return S3ObjectStorage(settings)


def knowledge_object_key(
    tenant_id: UUID,
    knowledge_base_id: UUID,
    source_id: UUID,
    media_type: str,
) -> str:
    extension = {
        PDF_MEDIA_TYPE: "pdf",
        XLSX_MEDIA_TYPE: "xlsx",
    }.get(media_type)
    if extension is None:
        raise ValueError("Unsupported knowledge media type")
    return f"knowledge/{tenant_id}/{knowledge_base_id}/{source_id}.{extension}"
