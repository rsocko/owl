"""Paperless-ngx integration for Statement Tracker — delegates to shared client."""

from __future__ import annotations

from doc_intelligence_hub.core.extractors.account_numbers import (
    normalize_masked_account_identifier,
)
from doc_intelligence_hub.core.paperless import (
    MetadataFieldKey,
    PaperlessClient,
    ResolvedMetadataSchema,
    load_fixture,
    resolve_metadata_value,
)
from doc_intelligence_hub.modules.statements.models import DocumentRecord


def load_fixture_documents(fixture_path: str) -> list[DocumentRecord]:
    raw = load_fixture(fixture_path)
    return [DocumentRecord.model_validate(item) for item in raw]


def build_document_records(
    raw_documents: list[dict],
    correspondents: dict[int, str],
    tags: dict[int, str],
    document_types: dict[int, str],
    *,
    metadata_schema: ResolvedMetadataSchema | None = None,
) -> list[DocumentRecord]:
    """Convert Paperless API documents into the statement analysis contract."""
    records: list[DocumentRecord] = []
    for item in raw_documents:
        raw_document_type = item.get("document_type")
        document_type_id = (
            raw_document_type
            if isinstance(raw_document_type, int) and not isinstance(raw_document_type, bool)
            else None
        )
        document_type = (
            document_types.get(document_type_id)
            if document_type_id is not None
            else str(raw_document_type)
            if raw_document_type
            else None
        )
        raw_tags = item.get("tags", [])
        tag_ids = [
            tag_id
            for tag_id in raw_tags
            if isinstance(tag_id, int) and not isinstance(tag_id, bool)
        ]
        tag_names = [
            tags.get(tag, str(tag)) if isinstance(tag, int) else str(tag) for tag in raw_tags
        ]
        account_identifier = None
        if metadata_schema is not None:
            custom_fields = item.get("custom_fields")
            if isinstance(custom_fields, list):
                resolved_account = resolve_metadata_value(
                    MetadataFieldKey.ACCOUNT_IDENTIFIER,
                    custom_fields,
                    metadata_schema,
                )
                account_identifier = normalize_masked_account_identifier(resolved_account.value)
        records.append(
            DocumentRecord(
                id=item["id"],
                title=item.get("title") or "Untitled",
                correspondent_id=item.get("correspondent"),
                correspondent_name=correspondents.get(item.get("correspondent"), "Unknown"),
                document_type_id=document_type_id,
                document_type=document_type,
                created=item["created_date"] if item.get("created_date") else item["created"],
                added=item.get("added"),
                tag_ids=tag_ids,
                tags=tag_names,
                original_file_name=item.get("original_file_name"),
                account_identifier=account_identifier,
                account_identifier_source="stored" if account_identifier else None,
            )
        )
    return records


async def test_paperless_connection(
    base_url: str,
    api_token: str,
    verify_ssl: bool = True,
    timeout_seconds: int = 30,
) -> dict[str, int | str]:
    client = PaperlessClient(
        base_url=base_url,
        token=api_token,
        verify_ssl=verify_ssl,
        timeout=float(timeout_seconds),
    )
    return await client.health_check()


async def fetch_paperless_documents(
    base_url: str,
    api_token: str,
    verify_ssl: bool = True,
    timeout_seconds: int = 30,
    on_progress: object = None,
) -> list[DocumentRecord]:
    client = PaperlessClient(
        base_url=base_url,
        token=api_token,
        verify_ssl=verify_ssl,
        timeout=float(timeout_seconds),
    )

    # Fetch metadata (correspondents + tags + document types) for enrichment
    correspondents, tags, document_types = await client.fetch_all_metadata(on_progress=on_progress)

    # Fetch all documents with progress
    raw_documents = await client.list_documents(on_progress=on_progress)

    if on_progress:
        await on_progress(
            "processing", "Processing documents...", len(raw_documents), len(raw_documents)
        )

    return build_document_records(raw_documents, correspondents, tags, document_types)
