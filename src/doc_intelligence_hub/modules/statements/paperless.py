"""Paperless-ngx integration for Statement Tracker — delegates to shared client."""

from __future__ import annotations

from doc_intelligence_hub.core.paperless import PaperlessClient, load_fixture
from doc_intelligence_hub.modules.statements.models import DocumentRecord


def load_fixture_documents(fixture_path: str) -> list[DocumentRecord]:
    raw = load_fixture(fixture_path)
    return [DocumentRecord.model_validate(item) for item in raw]


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

    # Fetch metadata (correspondents + tags) for enrichment
    correspondents, tags = await client.fetch_all_metadata(on_progress=on_progress)

    # Fetch all documents with progress
    raw_documents = await client.list_documents(on_progress=on_progress)

    if on_progress:
        await on_progress("processing", "Processing documents...", len(raw_documents), len(raw_documents))

    return [
        DocumentRecord(
            id=item["id"],
            title=item.get("title") or "Untitled",
            correspondent_id=item.get("correspondent"),
            correspondent_name=correspondents.get(item.get("correspondent"), "Unknown"),
            document_type=str(item.get("document_type")) if item.get("document_type") is not None else None,
            created=item["created_date"] if item.get("created_date") else item["created"],
            added=item.get("added"),
            tags=[tags.get(tag_id, str(tag_id)) for tag_id in item.get("tags", [])],
            original_file_name=item.get("original_file_name"),
        )
        for item in raw_documents
    ]

