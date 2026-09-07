import asyncio
import zipfile
from io import BytesIO
from uuid import uuid4

import pytest
from openpyxl import Workbook
from pydantic import SecretStr
from pypdf import PdfWriter

import customers_manager_hub.knowledge_parsing as parsing
from customers_manager_hub.config import Settings
from customers_manager_hub.knowledge_runtime import (
    KnowledgeRuntimeError,
    _validate_vector,  # pyright: ignore[reportPrivateUsage]
)
from customers_manager_hub.knowledge_storage import (
    MAX_KNOWLEDGE_OBJECT_BYTES,
    PDF_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    InMemoryObjectStorage,
    S3ObjectStorage,
    UnavailableObjectStorage,
    build_object_storage,
    knowledge_object_key,
)


def _xlsx_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = "Policy"
    worksheet.append(["Topic", "Rule"])
    worksheet.append(["Refund", "Returns are accepted within 30 days."])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()
    return buffer.getvalue()


def test_knowledge_object_key_is_server_scoped_and_path_safe() -> None:
    tenant_id = uuid4()
    base_id = uuid4()
    source_id = uuid4()
    key = knowledge_object_key(tenant_id, base_id, source_id, PDF_MEDIA_TYPE)
    assert key == f"knowledge/{tenant_id}/{base_id}/{source_id}.pdf"
    assert ".." not in key
    assert not key.startswith("/")


def test_storage_builder_requires_credentials_but_not_custom_endpoint() -> None:
    unavailable = build_object_storage(Settings(app_env="test"))
    assert isinstance(unavailable, UnavailableObjectStorage)

    aws_style = build_object_storage(
        Settings(
            app_env="test",
            s3_access_key_id=SecretStr("access"),
            s3_secret_access_key=SecretStr("secret"),
        )
    )
    assert isinstance(aws_style, S3ObjectStorage)


def test_in_memory_storage_enforces_object_size_limit() -> None:
    storage = InMemoryObjectStorage()

    async def run() -> None:
        await storage.put_bytes("knowledge/a/b/c.pdf", b"ok", PDF_MEDIA_TYPE)
        assert await storage.get_bytes("knowledge/a/b/c.pdf") == b"ok"
        with pytest.raises(ValueError, match="maximum size"):
            await storage.put_bytes(
                "knowledge/a/b/large.pdf",
                b"x" * (MAX_KNOWLEDGE_OBJECT_BYTES + 1),
                PDF_MEDIA_TYPE,
            )

    asyncio.run(run())


def test_xlsx_parsing_preserves_sheet_row_provenance_and_chunking_is_deterministic() -> None:
    units = parsing.parse_xlsx(_xlsx_bytes())
    assert units
    assert units[0].provenance["sheet"] == "Policy"
    assert units[0].provenance["row_start"] == 1
    assert units[0].provenance["row_end"] == 2
    assert "Returns are accepted within 30 days." in units[0].text

    first = parsing.chunk_units(units)
    second = parsing.chunk_units(units)
    assert first == second
    assert all(0 < len(chunk.content) <= parsing.CHUNK_MAX_CHARACTERS for chunk in first)
    assert all(chunk.provenance["sheet"] == "Policy" for chunk in first)


def test_xlsx_expanded_size_limit_is_checked_before_openpyxl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr("xl/data.bin", b"0123456789")
    monkeypatch.setattr(parsing, "MAX_XLSX_EXPANDED_BYTES", 5)
    with pytest.raises(parsing.KnowledgeParseError) as exc_info:
        parsing.parse_xlsx(buffer.getvalue())
    assert exc_info.value.code == "knowledge_xlsx_expanded_too_large"


def test_pdf_page_limit_is_enforced() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    original = parsing.MAX_PDF_PAGES
    parsing.MAX_PDF_PAGES = 0
    try:
        with pytest.raises(parsing.KnowledgeParseError) as exc_info:
            parsing.parse_pdf(buffer.getvalue())
        assert exc_info.value.code == "knowledge_pdf_too_many_pages"
    finally:
        parsing.MAX_PDF_PAGES = original


def test_pdf_text_provenance_and_normalization(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def extract_text(self) -> str:
            return "  Refunds   allowed\r\nwithin 30 days.  "

    class FakeReader:
        is_encrypted = False
        pages = [FakePage()]

        def __init__(self, source: BytesIO, strict: bool = False) -> None:
            del source, strict

    monkeypatch.setattr(parsing, "PdfReader", FakeReader)
    units = parsing.parse_pdf(b"fake")
    assert len(units) == 1
    assert units[0].text == "Refunds allowed\nwithin 30 days."
    assert units[0].provenance == {"page": 1}


def test_embedding_vector_validation_rejects_invalid_values() -> None:
    assert _validate_vector((1.0, 2.0)) == [1.0, 2.0]
    with pytest.raises(KnowledgeRuntimeError) as zero:
        _validate_vector((0.0, 0.0))
    assert zero.value.code == "knowledge_embedding_zero_vector"
    with pytest.raises(KnowledgeRuntimeError) as non_finite:
        _validate_vector((1.0, float("nan")))
    assert non_finite.value.code == "knowledge_embedding_invalid"


def test_media_type_constants_are_distinct() -> None:
    assert PDF_MEDIA_TYPE != XLSX_MEDIA_TYPE
