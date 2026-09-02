"""Command-line interface for the analysis invalidation / staleness mechanism (issue #114).

Commands:
    init-database              Create the invalidation database.
    simulate-version-change    Manually/programmatically simulate an accepted
                                OCR version change for one document — the
                                supported stand-in for issue #18's not-yet-built
                                "apply" step.
    invalidate                 Force-invalidate documents by scope (all,
                                low_confidence_failed, or specific IDs).
    status                     Show per-module fingerprint/staleness status
                                for one document.
"""

from __future__ import annotations

import json

import click
from rich.console import Console

from .config import settings
from .database import init_db
from .models import InvalidationReason
from .scopes import resolve_low_confidence_failed_document_ids
from .service import AnalysisFreshnessService

console = Console()


@click.group()
def cli() -> None:
    """Analysis invalidation / staleness mechanism (issue #114)."""
    from doc_intelligence_hub.core.logging_config import configure_logging

    configure_logging()


@cli.command("init-database")
def init_database() -> None:
    """Initialize the analysis invalidation SQLite database."""
    init_db()
    console.print("[green]OK Database initialized[/green]")


@cli.command("simulate-version-change")
@click.option("--document-id", type=int, required=True)
@click.option("--checksum", type=str, required=True, help="New accepted content checksum.")
@click.option("--title", type=str, default=None, help="Optional metadata field to fingerprint.")
@click.option("--correspondent", type=str, default=None)
@click.option("--document-type", type=str, default=None)
@click.option("--tag", "tags", multiple=True)
def simulate_version_change(
    document_id: int,
    checksum: str,
    title: str | None,
    correspondent: str | None,
    document_type: str | None,
    tags: tuple[str, ...],
) -> None:
    """Simulate "this document's accepted OCR version changed" for testing.

    There is no real trigger for this yet — issue #18's "apply an accepted
    OCR candidate" step will call the equivalent service method once built.
    """
    init_db()
    metadata_fields: dict[str, object] = {}
    if title is not None:
        metadata_fields["title"] = title
    if correspondent is not None:
        metadata_fields["correspondent"] = correspondent
    if document_type is not None:
        metadata_fields["document_type"] = document_type
    if tags:
        metadata_fields["tags"] = sorted(tags)

    service = AnalysisFreshnessService()
    result = service.simulate_version_change(
        document_id=document_id,
        new_checksum=checksum,
        metadata_fields=metadata_fields or None,
        triggered_by="simulated:cli",
    )
    console.print_json(json.dumps(result))


@cli.command("invalidate")
@click.option("--all", "invalidate_all", is_flag=True, help="Invalidate all known documents.")
@click.option(
    "--scope",
    type=click.Choice(["low_confidence_failed"]),
    default=None,
    help="Invalidate a named scope of documents.",
)
@click.option("--document-id", "document_ids", multiple=True, type=int, help="Specific document ID(s).")
@click.option("--limit", type=int, default=None, help="Cap on documents affected (bounded regardless).")
def invalidate(
    invalidate_all: bool,
    scope: str | None,
    document_ids: tuple[int, ...],
    limit: int | None,
) -> None:
    """Force invalidation for all documents, a named scope, or specific IDs."""
    chosen = [bool(invalidate_all), bool(scope), bool(document_ids)]
    if sum(chosen) != 1:
        raise click.ClickException("Specify exactly one of --all, --scope, or --document-id.")

    effective_limit = min(limit, settings.max_manual_invalidation_batch) if limit else (
        settings.max_manual_invalidation_batch
    )

    init_db()
    service = AnalysisFreshnessService()

    if invalidate_all:
        ids = service.list_known_document_ids(limit=effective_limit)
        reason = InvalidationReason.MANUAL_ALL
    elif scope == "low_confidence_failed":
        ids = resolve_low_confidence_failed_document_ids(limit=effective_limit)
        reason = InvalidationReason.MANUAL_SCOPE
    else:
        ids = list(document_ids)[:effective_limit]
        reason = InvalidationReason.MANUAL_DOCUMENT

    if not ids:
        console.print("[yellow]No documents matched the requested scope.[/yellow]")
        return

    result = service.manual_invalidate(document_ids=ids, reason=reason, triggered_by="manual:cli")
    console.print_json(json.dumps(result))


@cli.command()
@click.option("--document-id", type=int, required=True)
def status(document_id: int) -> None:
    """Show per-module fingerprint/staleness status for one document."""
    init_db()
    service = AnalysisFreshnessService()
    console.print_json(json.dumps(service.get_document_status(document_id)))


if __name__ == "__main__":
    cli()
