"""Command-line interface for the OCR quality baseline inventory (issue #25).

All commands are read-only against Paperless — this module never mutates
documents or metadata. `run` performs the Stage-1 full-corpus scan, `sample`
performs the Stage-2 deterministic stratified PDF sample + profiling, and
`report`/`status` render redacted, aggregate-only summaries.
"""

from __future__ import annotations

import asyncio
import json

import click
import httpx
from rich.console import Console
from rich.table import Table

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.core.resilience import PaperlessError

from .config import settings
from .database import InventoryRun, get_session, init_db
from .service import OcrQualityInventoryService

console = Console()


def _client(url: str, token: str) -> PaperlessClient:
    return PaperlessClient(base_url=url, token=token)


def _run_async(coroutine):
    try:
        return asyncio.run(coroutine)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except (PaperlessError, httpx.HTTPError) as exc:
        raise click.ClickException(f"Paperless operation failed: {exc}") from exc


def _connection_options(function):
    function = click.option(
        "--paperless-token",
        envvar="PAPERLESS_API_TOKEN",
        default=lambda: settings.paperless_api_token or None,
        required=True,
        help="Paperless API token (never emitted).",
    )(function)
    return click.option(
        "--paperless-url",
        envvar="PAPERLESS_URL",
        default=lambda: settings.paperless_url,
        required=True,
        help="Paperless base URL (never emitted).",
    )(function)


async def _resolve_scope_params(
    client: PaperlessClient, *, tags: tuple[str, ...], correspondent: str | None
) -> dict[str, object]:
    scope_params: dict[str, object] = {}
    if tags:
        all_tags = await client.list_tags()
        wanted = {t.lower() for t in tags}
        tag_ids = [str(t["id"]) for t in all_tags if str(t.get("name", "")).lower() in wanted]
        if tag_ids:
            scope_params["tags__id__in"] = ",".join(tag_ids)
    if correspondent:
        all_correspondents = await client.list_correspondents()
        match = next(
            (
                c
                for c in all_correspondents
                if str(c.get("name", "")).lower() == correspondent.lower()
            ),
            None,
        )
        if match:
            scope_params["correspondent__id"] = match["id"]
    return scope_params


@click.group()
def cli() -> None:
    """OCR baseline inventory — non-mutating Stage 1/2 corpus scan (issue #25)."""
    from doc_intelligence_hub.core.logging_config import configure_logging

    configure_logging()


@cli.command()
def init_database() -> None:
    """Initialize the OCR quality inventory SQLite database."""
    init_db()
    console.print("[green]OK Database initialized[/green]")


@cli.command()
@_connection_options
@click.option("--batch-size", type=click.IntRange(min=1, max=1000), default=settings.batch_size)
@click.option("--run-id", type=str, default=None, help="Explicit run ID (required to --resume).")
@click.option("--resume", is_flag=True, help="Resume a previously interrupted run by cursor.")
@click.option("--tag", "tags", multiple=True, help="Restrict scope to document(s) with this tag.")
@click.option("--correspondent", type=str, default=None, help="Restrict scope by correspondent.")
def run(
    paperless_url: str,
    paperless_token: str,
    batch_size: int,
    run_id: str | None,
    resume: bool,
    tags: tuple[str, ...],
    correspondent: str | None,
) -> None:
    """Stage 1 — resumable full-corpus text/metadata scan (read-only)."""

    async def _run() -> dict:
        init_db()
        async with _client(paperless_url, paperless_token) as client:
            scope_params = await _resolve_scope_params(
                client, tags=tags, correspondent=correspondent
            )
            service = OcrQualityInventoryService(client, get_session)
            return await service.run_corpus_scan(
                batch_size=batch_size,
                run_id=run_id,
                resume=resume,
                scope_params=scope_params or None,
            )

    result = _run_async(_run())
    console.print_json(json.dumps(result))


@cli.command()
@_connection_options
@click.option("--source-run-id", type=str, required=True, help="Stage-1 run ID to sample from.")
@click.option("--run-id", type=str, default=None, help="Explicit Stage-2 run ID.")
@click.option("--sample-size", type=int, default=settings.sample_target_size)
@click.option("--seed", type=str, default=settings.sample_seed)
@click.option("--min-per-stratum", type=int, default=settings.sample_min_per_stratum)
@click.option("--max-pages", type=int, default=settings.pdf_profile_max_pages)
def sample(
    paperless_url: str,
    paperless_token: str,
    source_run_id: str,
    run_id: str | None,
    sample_size: int,
    seed: str,
    min_per_stratum: int,
    max_pages: int,
) -> None:
    """Stage 2 — deterministic stratified PDF sample + page-aware profiling."""

    async def _run() -> dict:
        init_db()
        async with _client(paperless_url, paperless_token) as client:
            service = OcrQualityInventoryService(client, get_session)
            return await service.run_stratified_sample(
                source_run_id=source_run_id,
                sample_size=sample_size,
                seed=seed,
                min_per_stratum=min_per_stratum,
                pdf_profile_max_pages=max_pages,
                run_id=run_id,
            )

    result = _run_async(_run())
    console.print_json(json.dumps(result))


@cli.command()
@click.option("--run-id", type=str, required=True)
def report(run_id: str) -> None:
    """Render a redacted, aggregate-only report for a run (no PDFs needed)."""
    init_db()

    # build_aggregate_report only needs a session factory — no live Paperless
    # connection is required to render a report for a completed run.
    service = OcrQualityInventoryService(None, get_session)  # type: ignore[arg-type]
    result = service.build_aggregate_report(run_id)
    console.print_json(json.dumps(result))


@cli.command()
def status() -> None:
    """List known inventory runs and their progress."""
    init_db()
    db = get_session()
    try:
        runs = db.query(InventoryRun).order_by(InventoryRun.started_at.desc()).limit(20).all()
        if not runs:
            console.print("[dim]No OCR quality inventory runs yet.[/dim]")
            return
        table = Table(show_header=True, title="OCR Quality Inventory Runs")
        table.add_column("Run ID")
        table.add_column("Stage")
        table.add_column("Status")
        table.add_column("Counts")
        table.add_column("Started")
        for r in runs:
            table.add_row(
                r.run_id,
                r.stage,
                r.status,
                json.dumps(r.counts or {}),
                r.started_at.isoformat() if r.started_at else "-",
            )
        console.print(table)
    finally:
        db.close()


if __name__ == "__main__":
    cli()
