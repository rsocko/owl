"""CLI for EOB Matching — fetch from Paperless, classify, extract, match, persist."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.eob_matching.classifier import classify_document
from doc_intelligence_hub.modules.eob_matching.extractor import extract_eob, extract_bill
from doc_intelligence_hub.modules.eob_matching.llm_extractor import extract_eob_llm, extract_bill_llm
from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import DocumentType, MatchConfidence

console = Console()


def _init_database(db_url: str | None = None) -> None:
    """Configure and initialize the database."""
    from doc_intelligence_hub.modules.eob_matching import database

    if db_url:
        database.configure(db_url)
    database.init_db()


@click.group()
@click.option(
    "--db-url",
    envvar="EOB_DATABASE_URL",
    default=None,
    help="SQLite database URL (default: sqlite:///data/eob_matching.db)",
)
@click.pass_context
def cli(ctx, db_url):
    """EOB Matching — classify, extract, and match medical documents."""
    ctx.ensure_object(dict)
    ctx.obj["db_url"] = db_url


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True, help="Paperless-ngx URL")
@click.option("--paperless-token", envvar="PAPERLESS_API_TOKEN", required=True, help="Paperless API token")
@click.option("--tag", multiple=True, help="Filter by tag name (can specify multiple)")
@click.option("--correspondent", type=str, default=None, help="Filter by correspondent name")
@click.option("--document-type", type=str, default=None, help="Filter by Paperless document type")
@click.option("--created-after", type=str, default=None, help="Only docs created on/after this date (YYYY-MM-DD)")
@click.option("--created-before", type=str, default=None, help="Only docs created on/before this date (YYYY-MM-DD)")
@click.option("--limit", type=int, default=50, help="Max documents to process")
@click.option("--output", type=click.Path(), default=None, help="Save results to JSON file")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed extraction results")
@click.option("--write-to-paperless", is_flag=True, envvar="WRITE_TO_PAPERLESS",
              help="Write match results back to Paperless custom fields")
@click.option("--skip-processed/--no-skip-processed", default=True,
              help="Skip documents that were successfully extracted in a prior run (default: enabled)")
@click.option("--use-llm/--no-llm", default=None,
              help="Use LLM extractor (default: auto-detect from LLM_BASE_URL)")
@click.pass_context
def run(ctx, paperless_url, paperless_token, tag, correspondent, document_type, created_after, created_before, limit, output, verbose, write_to_paperless, skip_processed, use_llm):
    """Run the full pipeline: fetch → classify → extract → match → store.

    Results are persisted to SQLite. Use --write-to-paperless to also
    write match metadata back to Paperless custom fields.

    Examples:\b
        eob-match run --tag medical --limit 20
        eob-match run --correspondent "UnitedHealthcare" --verbose
        eob-match run --created-after 2026-01-01 --limit 50
        eob-match run --document-type "EOB - Explanation of Benefits" --created-after 2026-06-01
    """
    _init_database(ctx.obj.get("db_url"))
    asyncio.run(_run_pipeline(
        paperless_url=paperless_url,
        paperless_token=paperless_token,
        tags=list(tag) if tag else None,
        correspondent=correspondent,
        document_type=document_type,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        output_path=output,
        verbose=verbose,
        write_to_paperless=write_to_paperless,
        skip_processed=skip_processed,
        use_llm=use_llm,
    ))


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True)
@click.option("--paperless-token", envvar="PAPERLESS_API_TOKEN", required=True)
@click.option("--tag", multiple=True, help="Filter by tag name")
@click.option("--document-type", type=str, default=None, help="Filter by Paperless document type")
@click.option("--created-after", type=str, default=None, help="Only docs created on/after this date (YYYY-MM-DD)")
@click.option("--created-before", type=str, default=None, help="Only docs created on/before this date (YYYY-MM-DD)")
@click.option("--limit", type=int, default=20, help="Max documents to scan")
def classify(paperless_url, paperless_token, tag, document_type, created_after, created_before, limit):
    """Classify documents as EOB, Bill, or Unknown (read-only).

    Fetches documents and shows classification results without modifying anything.

    Examples:\b
        eob-match classify --tag medical --limit 10
        eob-match classify --created-after 2026-01-01 --limit 50
        eob-match classify --document-type "EOB - Explanation of Benefits" --limit 20
    """
    asyncio.run(_classify_only(
        paperless_url=paperless_url,
        paperless_token=paperless_token,
        tags=list(tag) if tag else None,
        document_type=document_type,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
    ))


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True)
@click.option("--paperless-token", envvar="PAPERLESS_API_TOKEN", required=True)
def check(paperless_url, paperless_token):
    """Verify connectivity to Paperless-ngx."""
    asyncio.run(_check_connection(paperless_url, paperless_token))


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True)
@click.option("--paperless-token", envvar="PAPERLESS_API_TOKEN", required=True)
def setup(paperless_url, paperless_token):
    """Create EOB matching custom fields in Paperless.

    Run this once before enabling --write-to-paperless to ensure
    all required custom fields exist.
    """
    asyncio.run(_setup_fields(paperless_url, paperless_token))


@cli.command("init-db")
@click.pass_context
def init_db(ctx):
    """Initialize the SQLite database (create tables)."""
    _init_database(ctx.obj.get("db_url"))
    console.print("[green]✓[/green] Database initialized")


@cli.command("purge-stale")
@click.option("--dry-run", is_flag=True, help="Show stale records without deleting them")
@click.pass_context
def purge_stale(ctx, dry_run):
    """Purge stale EOB records with garbage extracted data.

    Identifies records where the provider_name field contains document
    boilerplate text (e.g. "The summary below is intended to help you
    understand") that was incorrectly extracted before validation was added.

    Criteria for stale records:
    - provider_name contains >8 words
    - provider_name matches known boilerplate phrases
    - all amount fields are 0/null with suspiciously long provider name

    Examples:\b
        eob-match purge-stale --dry-run
        eob-match purge-stale
    """
    from doc_intelligence_hub.modules.eob_matching.database import get_session as get_db_session
    from doc_intelligence_hub.modules.eob_matching.purge import find_stale_eobs, purge_stale_eobs

    _init_database(ctx.obj.get("db_url"))
    db = get_db_session()

    try:
        if dry_run:
            stale = find_stale_eobs(db)
            if not stale:
                console.print("[green]✓[/green] No stale EOB records found.")
                return

            console.print(f"[yellow]Found {len(stale)} stale EOB records:[/yellow]\n")
            table = Table(title="Stale EOB Records")
            table.add_column("ID", style="dim")
            table.add_column("Doc ID")
            table.add_column("Provider Name")
            table.add_column("Amounts")
            for r in stale:
                amounts = f"B:{r.total_billed or 0} A:{r.total_allowed or 0} P:{r.total_plan_pays or 0} R:{r.total_patient_responsibility or 0}"
                provider_display = (r.provider_name or "")[:60]
                if len(r.provider_name or "") > 60:
                    provider_display += "..."
                table.add_row(str(r.id), str(r.document_id), provider_display, amounts)
            console.print(table)
            console.print("\n[dim]Run without --dry-run to delete these records.[/dim]")
        else:
            result = purge_stale_eobs(db)
            if result.purged_count == 0:
                console.print("[green]✓[/green] No stale EOB records found.")
            else:
                console.print(
                    f"[green]✓[/green] Purged {result.purged_count} stale EOB records "
                    f"and {result.orphaned_matches_removed} orphaned match records."
                )
                console.print(f"  Document IDs: {result.document_ids}")
    finally:
        db.close()


@cli.command()
@click.option("--models", type=str, required=True,
              help="Comma-separated model names (e.g. phi3:mini,llama3.1:8b,gpt-4o-mini)")
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True, help="Paperless-ngx URL")
@click.option("--paperless-token", envvar="PAPERLESS_API_TOKEN", required=True, help="Paperless API token")
@click.option("--tag", multiple=True, help="Filter by tag name")
@click.option("--document-type", type=str, default=None, help="Filter by Paperless document type")
@click.option("--created-after", type=str, default=None, help="Only docs created on/after this date (YYYY-MM-DD)")
@click.option("--created-before", type=str, default=None, help="Only docs created on/before this date (YYYY-MM-DD)")
@click.option("--limit", type=int, default=5, help="Number of documents to test per model")
@click.option("--output", type=click.Path(), default=None, help="Save results to JSON file")
@click.option("--bifrost-url", envvar="LLM_BASE_URL",
              default="https://service-001.example.invalid/openai/v1",
              help="Bifrost gateway URL")
def benchmark(models, paperless_url, paperless_token, tag, document_type, created_after, created_before, limit, output, bifrost_url):
    """Benchmark LLM models on EOB extraction for speed and accuracy.

    Fetches real EOB documents from Paperless and runs each model against them,
    comparing extraction time, success rate, and confidence scores.

    Examples:\b
        eob-match benchmark --models phi3:mini,llama3.1:8b,gpt-4o-mini --limit 5
        eob-match benchmark --models gpt-4o,gpt-4o-mini --limit 10
        eob-match benchmark --models phi3:mini,mistral-nemo:latest --output results.json
    """
    asyncio.run(_run_benchmark(
        models_str=models,
        paperless_url=paperless_url,
        paperless_token=paperless_token,
        tags=list(tag) if tag else None,
        document_type=document_type,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
        output_path=output,
        bifrost_url=bifrost_url,
    ))


@cli.command()
@click.option("--dry-run", is_flag=True, help="Report duplicates without removing them")
@click.pass_context
def dedup(ctx, dry_run):
    """Remove duplicate records from prior runs.

    Finds duplicate EOB/Bill records (same document_id across multiple run_ids),
    keeps the most recent (highest run_id), and removes older duplicates.
    Also consolidates duplicate match records (same EOB↔Bill pair across runs).
    """
    _init_database(ctx.obj.get("db_url"))

    from doc_intelligence_hub.modules.eob_matching.database import (
        EOBRecord, BillRecord, MatchRecord,
        get_session as get_db_session,
    )
    from sqlalchemy import func

    db = get_db_session()

    console.print(Panel("[bold]EOB Matching — Cross-Run Deduplication[/bold]"
                        + (" [dim](dry run)[/dim]" if dry_run else ""), style="blue"))
    console.print()

    # --- Deduplicate EOBRecords ---
    dup_eobs = (
        db.query(EOBRecord.document_id)
        .group_by(EOBRecord.document_id)
        .having(func.count(EOBRecord.id) > 1)
        .all()
    )
    eob_removed = 0
    for (doc_id,) in dup_eobs:
        records = (
            db.query(EOBRecord)
            .filter_by(document_id=doc_id)
            .order_by(EOBRecord.run_id.desc())
            .all()
        )
        # Keep the first (most recent run_id), remove the rest
        for old in records[1:]:
            if not dry_run:
                db.delete(old)
            eob_removed += 1

    # --- Deduplicate BillRecords ---
    dup_bills = (
        db.query(BillRecord.document_id)
        .group_by(BillRecord.document_id)
        .having(func.count(BillRecord.id) > 1)
        .all()
    )
    bill_removed = 0
    for (doc_id,) in dup_bills:
        records = (
            db.query(BillRecord)
            .filter_by(document_id=doc_id)
            .order_by(BillRecord.run_id.desc())
            .all()
        )
        for old in records[1:]:
            if not dry_run:
                db.delete(old)
            bill_removed += 1

    # --- Deduplicate MatchRecords ---
    dup_matches = (
        db.query(MatchRecord.eob_document_id, MatchRecord.bill_document_id)
        .group_by(MatchRecord.eob_document_id, MatchRecord.bill_document_id)
        .having(func.count(MatchRecord.id) > 1)
        .all()
    )
    match_removed = 0
    for eob_doc_id, bill_doc_id in dup_matches:
        records = (
            db.query(MatchRecord)
            .filter_by(eob_document_id=eob_doc_id, bill_document_id=bill_doc_id)
            .order_by(MatchRecord.run_id.desc())
            .all()
        )
        # Keep the most recent, preferring confirmed status
        records.sort(key=lambda r: (r.status == "confirmed", r.run_id or 0), reverse=True)
        for old in records[1:]:
            if not dry_run:
                db.delete(old)
            match_removed += 1

    if not dry_run:
        db.commit()
    db.close()

    action = "Would remove" if dry_run else "Removed"
    console.print(f"  EOB records:   {action} {eob_removed} duplicate(s) across {len(dup_eobs)} document(s)")
    console.print(f"  Bill records:  {action} {bill_removed} duplicate(s) across {len(dup_bills)} document(s)")
    console.print(f"  Match records: {action} {match_removed} duplicate(s) across {len(dup_matches)} pair(s)")

    total = eob_removed + bill_removed + match_removed
    if total == 0:
        console.print("\n  [green]No duplicates found — database is clean.[/green]")
    elif dry_run:
        console.print(f"\n  [yellow]Run without --dry-run to remove {total} duplicate(s).[/yellow]")
    else:
        console.print(f"\n  [green]✓ Cleaned up {total} duplicate record(s).[/green]")


@cli.command()
@click.option("--last", type=int, default=10, help="Number of recent runs to show")
@click.pass_context
def status(ctx, last):
    """Show recent pipeline runs and match statistics."""
    _init_database(ctx.obj.get("db_url"))
    _show_status(last)


async def _check_connection(url: str, token: str):
    """Test Paperless connection."""
    console.print("[bold]EOB Matching — Connectivity Check[/bold]\n")
    client = PaperlessClient(base_url=url, token=token)
    try:
        result = await client.health_check()
        console.print(f"  [green]✓[/green] Connected to {result['base_url']}")
        console.print(f"  Documents: {result['documents']}")
        console.print(f"  Correspondents: {result['correspondents']}")
        console.print(f"  Tags: {result['tags']}")
    except Exception as e:
        console.print(f"  [red]✗ Connection failed:[/red] {e}")


async def _setup_fields(url: str, token: str):
    """Create EOB custom fields in Paperless."""
    from doc_intelligence_hub.modules.eob_matching.enricher import EOBEnricher

    console.print("[bold]EOB Matching — Custom Field Setup[/bold]\n")
    client = PaperlessClient(base_url=url, token=token)
    enricher = EOBEnricher(client)
    try:
        field_map = await enricher.ensure_custom_fields_exist()
        for name, fid in field_map.items():
            console.print(f"  [green]✓[/green] {name} (id={fid})")
        console.print(f"\n  {len(field_map)} custom fields ready")
    except Exception as e:
        console.print(f"  [red]✗ Setup failed:[/red] {e}")


def _show_status(last: int):
    """Display recent run history and match stats."""
    from doc_intelligence_hub.modules.eob_matching.database import (
        get_session,
        latest_runs,
        pending_matches,
        confirmed_matches,
    )

    db = get_session()
    try:
        runs = latest_runs(db, limit=last)
        candidates = pending_matches(db)
        confirmed = confirmed_matches(db)

        if not runs:
            console.print("[yellow]No matching runs found. Run `eob-match run` first.[/yellow]")
            return

        table = Table(title="Recent Matching Runs", show_lines=True)
        table.add_column("ID", width=4)
        table.add_column("Date", width=20)
        table.add_column("Scanned", width=8, justify="right")
        table.add_column("EOBs", width=6, justify="right")
        table.add_column("Bills", width=6, justify="right")
        table.add_column("Matches", width=8, justify="right")
        table.add_column("H/M/L", width=10)

        for r in runs:
            date_str = r.started_at.strftime("%Y-%m-%d %H:%M") if r.started_at else "?"
            table.add_row(
                str(r.id),
                date_str,
                str(r.documents_scanned),
                str(r.eobs_found),
                str(r.bills_found),
                str(r.matches_found),
                f"{r.high_confidence}/{r.medium_confidence}/{r.low_confidence}",
            )

        console.print(table)
        console.print(f"\n  Candidate matches: {len(candidates)} | Confirmed: {len(confirmed)}")
    finally:
        db.close()


async def _classify_only(
    paperless_url: str,
    paperless_token: str,
    tags: list[str] | None,
    document_type: str | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
):
    """Fetch and classify documents."""
    client = PaperlessClient(base_url=paperless_url, token=paperless_token)

    console.print("[bold]EOB Matching — Document Classification[/bold]\n")
    console.print(f"[dim]Fetching up to {limit} documents...[/dim]")

    documents = await client.list_documents(
        tags=tags,
        document_type=document_type,
        created_after=created_after,
        created_before=created_before,
        page_size=min(limit, 100),
        limit=limit,
    )
    documents = documents[:limit]

    console.print(f"[green]✓[/green] Fetched {len(documents)} documents\n")

    table = Table(title="Classification Results", show_lines=True)
    table.add_column("ID", style="bold", width=6)
    table.add_column("Title", max_width=45)
    table.add_column("Type", width=8)
    table.add_column("Score", width=6, justify="right")
    table.add_column("Top Indicators", max_width=40)

    counts = {"EOB": 0, "BILL": 0, "UNKNOWN": 0}

    for doc in documents:
        content = doc.get("content", "")
        title = doc.get("title", "Untitled")

        result = classify_document(content)
        type_str = result.type.value
        counts[type_str] += 1

        style = {"EOB": "cyan", "BILL": "yellow", "UNKNOWN": "dim"}.get(type_str, "")
        indicators = ", ".join(result.indicators_matched[:3]) if result.indicators_matched else "—"

        table.add_row(
            str(doc["id"]),
            title[:45],
            f"[{style}]{type_str}[/{style}]",
            f"{result.confidence_score:.0f}",
            indicators,
        )

    console.print(table)
    console.print(f"\n  EOBs: {counts['EOB']} | Bills: {counts['BILL']} | Unknown: {counts['UNKNOWN']}")


async def _run_pipeline(
    paperless_url: str,
    paperless_token: str,
    tags: list[str] | None,
    correspondent: str | None,
    document_type: str | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
    output_path: str | None,
    verbose: bool,
    write_to_paperless: bool = False,
    skip_processed: bool = True,
    use_llm: bool | None = None,
):
    """Full pipeline: fetch → classify → extract → match → store."""
    # Auto-detect LLM availability if not explicitly set
    if use_llm is None:
        use_llm = bool(os.environ.get("LLM_BASE_URL"))

    from doc_intelligence_hub.modules.eob_matching.database import (
        MatchingRun, EOBRecord, BillRecord, MatchRecord,
        get_session as get_db_session, store_run, store_eob, store_bill, store_match,
    )

    client = PaperlessClient(base_url=paperless_url, token=paperless_token)
    db = get_db_session()

    # Create run record
    run_record = MatchingRun(
        tags_filter=",".join(tags) if tags else None,
        correspondent_filter=correspondent,
    )
    store_run(db, run_record)

    extractor_label = "[magenta]LLM extractor[/magenta]" if use_llm else "[dim]regex extractor[/dim]"
    console.print(Panel(
        f"[bold]EOB Matching Pipeline[/bold] (run #{run_record.id})"
        + (" — [green]LIVE mode[/green]" if write_to_paperless else " — [dim]read-only[/dim]")
        + f" — {extractor_label}",
        style="blue",
    ))
    console.print()

    # Step 1: Fetch documents
    console.print("[bold]Step 1:[/bold] Fetching documents...")
    documents = await client.list_documents(
        tags=tags,
        correspondent=correspondent,
        document_type=document_type,
        created_after=created_after,
        created_before=created_before,
        page_size=min(limit, 100),
        limit=limit,
    )
    documents = documents[:limit]
    console.print(f"  [green]✓[/green] {len(documents)} documents fetched\n")

    if not documents:
        console.print("[yellow]No documents found matching filters.[/yellow]")
        return

    # Step 2: Classify
    console.print("[bold]Step 2:[/bold] Classifying documents...")
    eob_docs = []
    bill_docs = []
    unknown_docs = []

    for doc in documents:
        content = doc.get("content", "")
        result = classify_document(content)

        if result.type == DocumentType.EOB:
            eob_docs.append((doc, result))
        elif result.type == DocumentType.BILL:
            bill_docs.append((doc, result))
        else:
            unknown_docs.append(doc)

    console.print(f"  [cyan]EOBs:[/cyan] {len(eob_docs)} | [yellow]Bills:[/yellow] {len(bill_docs)} | [dim]Unknown:[/dim] {len(unknown_docs)}\n")

    if not eob_docs and not bill_docs:
        console.print("[yellow]No medical documents classified. Try broader filters or more documents.[/yellow]")
        run_record.documents_scanned = len(documents)
        run_record.finished_at = datetime.now(UTC)
        db.commit()
        db.close()
        return

    # Step 3: Extract
    console.print("[bold]Step 3:[/bold] Extracting structured data...")

    # Track original classified counts before filtering
    classified_eob_count = len(eob_docs)
    classified_bill_count = len(bill_docs)
    skipped_eobs = 0
    skipped_bills = 0

    # Skip documents already processed in prior runs
    if skip_processed:
        existing_eob_doc_ids = {r.document_id for r in db.query(EOBRecord.document_id).all()}
        existing_bill_doc_ids = {r.document_id for r in db.query(BillRecord.document_id).all()}

        eob_docs = [(doc, cls) for doc, cls in eob_docs if doc["id"] not in existing_eob_doc_ids]
        bill_docs = [(doc, cls) for doc, cls in bill_docs if doc["id"] not in existing_bill_doc_ids]

        skipped_eobs = classified_eob_count - len(eob_docs)
        skipped_bills = classified_bill_count - len(bill_docs)
        if skipped_eobs or skipped_bills:
            console.print(f"  [dim]Skipped {skipped_eobs} EOBs and {skipped_bills} bills (already processed)[/dim]")

    extracted_eobs = []
    extracted_bills = []

    for doc, classification in eob_docs:
        content = doc.get("content", "")
        extracted = extract_eob_llm(content, document_id=str(doc["id"])) if use_llm else extract_eob(content, document_id=str(doc["id"]))
        extracted_eobs.append(extracted)

        # Persist EOB record
        store_eob(db, EOBRecord(
            document_id=doc["id"],
            run_id=run_record.id,
            title=doc.get("title"),
            classification_score=classification.confidence_score,
            insurance_company=extracted.insurance_company,
            policy_number=extracted.policy_number,
            patient_name=extracted.patient_name,
            claim_number=extracted.claim_number,
            date_of_service=str(extracted.date_of_service) if extracted.date_of_service else None,
            provider_name=extracted.provider_name,
            total_billed=extracted.total_billed,
            total_allowed=extracted.total_allowed,
            total_plan_pays=extracted.total_plan_pays,
            total_patient_responsibility=extracted.total_patient_responsibility,
            services_json=json.dumps([s.model_dump(mode="json") for s in extracted.services]) if extracted.services else None,
            last_processed_at=datetime.now(UTC),
        ))

        if verbose:
            console.print(f"  [cyan]EOB #{doc['id']}[/cyan]: provider={extracted.provider_name or '?'}, "
                         f"patient={extracted.patient_name or '?'}, "
                         f"date={extracted.date_of_service or '?'}, "
                         f"patient_resp=${extracted.total_patient_responsibility or 0:.2f}")

    for doc, classification in bill_docs:
        content = doc.get("content", "")
        extracted = extract_bill_llm(content, document_id=str(doc["id"])) if use_llm else extract_bill(content, document_id=str(doc["id"]))
        extracted_bills.append(extracted)

        # Persist Bill record
        store_bill(db, BillRecord(
            document_id=doc["id"],
            run_id=run_record.id,
            title=doc.get("title"),
            classification_score=classification.confidence_score,
            provider_name=extracted.provider_name,
            patient_name=extracted.patient_name,
            invoice_number=extracted.invoice_number,
            date_of_service=str(extracted.date_of_service) if extracted.date_of_service else None,
            due_date=str(extracted.due_date) if extracted.due_date else None,
            total_amount=extracted.total_amount,
            balance_due=extracted.balance_due,
            payment_status=extracted.payment_status,
            services_json=json.dumps([s.model_dump(mode="json") for s in extracted.services]) if extracted.services else None,
            last_processed_at=datetime.now(UTC),
        ))

        if verbose:
            console.print(f"  [yellow]Bill #{doc['id']}[/yellow]: provider={extracted.provider_name or '?'}, "
                         f"patient={extracted.patient_name or '?'}, "
                         f"date={extracted.date_of_service or '?'}, "
                         f"balance=${extracted.balance_due or 0:.2f}")

    console.print(f"  [green]✓[/green] Extracted {len(extracted_eobs)} EOBs, {len(extracted_bills)} bills\n")

    # Step 4: Match
    console.print("[bold]Step 4:[/bold] Running matching engine...")
    matches = match_documents(extracted_eobs, extracted_bills)

    # Persist match records (skip pairs already confirmed in a prior run)
    skipped_matches = 0
    stored_matches = []
    for match in matches:
        existing = db.query(MatchRecord).filter_by(
            eob_document_id=int(match.eob_id),
            bill_document_id=int(match.bill_id),
            status="confirmed",
        ).first()
        if existing:
            skipped_matches += 1
            continue

        stored_matches.append(match)
        store_match(db, MatchRecord(
            run_id=run_record.id,
            eob_document_id=int(match.eob_id),
            bill_document_id=int(match.bill_id),
            score=match.score,
            confidence=match.confidence.value,
            breakdown_date=match.breakdown.date,
            breakdown_provider=match.breakdown.provider,
            breakdown_patient=match.breakdown.patient,
            breakdown_amount=match.breakdown.amount,
            breakdown_procedures=match.breakdown.procedures,
            status="candidate",
        ))

    if skipped_matches:
        console.print(f"  [dim]Skipped {skipped_matches} match(es) already confirmed in prior runs[/dim]")

    if not matches:
        console.print("  [yellow]No matches found.[/yellow]")
        console.print("  [dim]This could mean documents don't have overlapping providers/dates,[/dim]")
        console.print("  [dim]or extraction didn't capture enough data for matching.[/dim]\n")
    else:
        console.print(f"  [green]✓[/green] Found {len(matches)} match(es)\n")

        table = Table(title="Match Results", show_lines=True)
        table.add_column("EOB", style="cyan", width=8)
        table.add_column("Bill", style="yellow", width=8)
        table.add_column("Score", width=7, justify="right")
        table.add_column("Confidence", width=10)
        table.add_column("Date", width=6, justify="right")
        table.add_column("Provider", width=8, justify="right")
        table.add_column("Patient", width=8, justify="right")
        table.add_column("Amount", width=8, justify="right")
        table.add_column("CPT", width=5, justify="right")

        for match in sorted(matches, key=lambda m: m.score, reverse=True):
            conf_style = {
                MatchConfidence.HIGH: "green",
                MatchConfidence.MEDIUM: "yellow",
                MatchConfidence.LOW: "red",
            }.get(match.confidence, "dim")

            table.add_row(
                f"#{match.eob_id}",
                f"#{match.bill_id}",
                f"{match.score:.1f}",
                f"[{conf_style}]{match.confidence.value}[/{conf_style}]",
                f"{match.breakdown.date:.0f}",
                f"{match.breakdown.provider:.0f}",
                f"{match.breakdown.patient:.0f}",
                f"{match.breakdown.amount:.0f}",
                f"{match.breakdown.procedures:.0f}",
            )

        console.print(table)

    # Step 4b: Write to Paperless (optional — only for newly stored matches)
    if write_to_paperless and stored_matches:
        from doc_intelligence_hub.modules.eob_matching.enricher import EOBEnricher

        console.print("\n[bold]Step 4b:[/bold] Writing match results to Paperless...")
        enricher = EOBEnricher(client)
        eob_lookup = {e.document_id: e for e in extracted_eobs}
        linked = 0
        for match in stored_matches:
            try:
                eob_data = eob_lookup.get(match.eob_id)
                patient_resp = eob_data.total_patient_responsibility if eob_data else None
                await enricher.link_match(
                    eob_document_id=int(match.eob_id),
                    bill_document_id=int(match.bill_id),
                    score=match.score,
                    confidence=match.confidence.value,
                    patient_responsibility=patient_resp,
                )
                linked += 1
                console.print(f"  [green]✓[/green] Linked EOB #{match.eob_id} ↔ Bill #{match.bill_id}")
            except Exception as e:
                console.print(f"  [red]✗[/red] Failed to link #{match.eob_id} ↔ #{match.bill_id}: {e}")
        console.print(f"  Linked {linked}/{len(stored_matches)} matches in Paperless")

    # Step 5: Summary
    console.print()
    high = sum(1 for m in stored_matches if m.confidence == MatchConfidence.HIGH)
    medium = sum(1 for m in stored_matches if m.confidence == MatchConfidence.MEDIUM)
    low = sum(1 for m in stored_matches if m.confidence == MatchConfidence.LOW)
    unmatched_eobs = len(extracted_eobs) - len(set(m.eob_id for m in matches))
    unmatched_bills = len(extracted_bills) - len(set(m.bill_id for m in matches))

    # Update run record
    run_record.documents_scanned = len(documents)
    run_record.eobs_found = classified_eob_count
    run_record.bills_found = classified_bill_count
    run_record.matches_found = len(stored_matches)
    run_record.high_confidence = high
    run_record.medium_confidence = medium
    run_record.low_confidence = low
    run_record.finished_at = datetime.now(UTC)
    db.commit()
    db.close()

    console.print(Panel(
        f"[bold]Summary (Run #{run_record.id})[/bold]\n"
        f"  Documents scanned: {len(documents)}\n"
        f"  Classified: {classified_eob_count} EOBs, {classified_bill_count} bills, {len(unknown_docs)} unknown\n"
        f"  Extracted: {len(extracted_eobs)} EOBs, {len(extracted_bills)} bills"
        + (f" (skipped {skipped_eobs + skipped_bills} already processed)" if skip_processed and (skipped_eobs + skipped_bills) else "") + "\n"
        f"  Matches: {high} high, {medium} medium, {low} low confidence"
        + (f" (skipped {skipped_matches} already confirmed)" if skipped_matches else "") + "\n"
        f"  Unmatched: {unmatched_eobs} EOBs, {unmatched_bills} bills waiting\n"
        f"  Results persisted to database",
        style="green" if stored_matches else "yellow",
    ))

    # Save output if requested
    if output_path:
        output_data = {
            "run_id": run_record.id,
            "run_at": datetime.now(UTC).isoformat(),
            "documents_scanned": len(documents),
            "classifications": {
                "eob": [{"id": d["id"], "title": d.get("title")} for d, _ in eob_docs],
                "bill": [{"id": d["id"], "title": d.get("title")} for d, _ in bill_docs],
                "unknown": [{"id": d["id"], "title": d.get("title")} for d in unknown_docs],
            },
            "matches": [
                {
                    "eob_id": m.eob_id,
                    "bill_id": m.bill_id,
                    "score": m.score,
                    "confidence": m.confidence.value,
                    "breakdown": {
                        "date": m.breakdown.date,
                        "provider": m.breakdown.provider,
                        "patient": m.breakdown.patient,
                        "amount": m.breakdown.amount,
                        "procedures": m.breakdown.procedures,
                    },
                }
                for m in matches
            ],
            "unmatched_eobs": [e.document_id for e in extracted_eobs if e.document_id not in {m.eob_id for m in matches}],
            "unmatched_bills": [b.document_id for b in extracted_bills if b.document_id not in {m.bill_id for m in matches}],
        }
        Path(output_path).write_text(json.dumps(output_data, indent=2), encoding="utf-8")
        console.print(f"\n[dim]Results saved to {output_path}[/dim]")


async def _run_benchmark(
    models_str: str,
    paperless_url: str,
    paperless_token: str,
    tags: list[str] | None,
    document_type: str | None,
    created_after: str | None,
    created_before: str | None,
    limit: int,
    output_path: str | None,
    bifrost_url: str,
):
    """Run model comparison benchmark."""
    from doc_intelligence_hub.core.llm import get_llm_settings, reset_llm_client, LLMSettings
    from doc_intelligence_hub.modules.eob_matching.benchmark import (
        benchmark_to_json,
        fetch_eob_documents,
        format_benchmark_table,
        run_benchmark,
    )

    models = [m.strip() for m in models_str.split(",") if m.strip()]
    if not models:
        console.print("[red]✗ No models specified[/red]")
        return

    console.print("[bold]EOB Matching — Model Benchmark[/bold]\n")
    console.print(f"  Models: {', '.join(models)}")
    console.print(f"  Documents: {limit}")
    console.print(f"  Bifrost URL: {bifrost_url}\n")

    # Configure LLM client to use specified Bifrost URL
    os.environ["LLM_BASE_URL"] = bifrost_url
    reset_llm_client()

    # Fetch documents
    console.print("[dim]Fetching documents from Paperless...[/dim]")
    try:
        documents = await fetch_eob_documents(
            paperless_url=paperless_url,
            paperless_token=paperless_token,
            limit=limit,
            tags=tags,
            document_type=document_type,
            created_after=created_after,
            created_before=created_before,
        )
    except Exception as e:
        console.print(f"[red]✗ Failed to fetch documents:[/red] {e}")
        return

    if not documents:
        console.print("[yellow]No documents found matching criteria.[/yellow]")
        return

    console.print(f"[green]✓[/green] Fetched {len(documents)} documents\n")
    console.print("[dim]Running benchmark (this may take a while)...[/dim]\n")

    # Run benchmark
    summaries = await run_benchmark(documents, models)

    # Display results
    console.print(Panel("[bold]Benchmark Results[/bold]"))
    console.print()

    # Rich table output
    table = Table(title="Model Comparison", show_lines=True)
    table.add_column("Model", style="bold", width=22)
    table.add_column("Avg Time (s)", justify="right", width=12)
    table.add_column("Success %", justify="right", width=10)
    table.add_column("Avg Confidence", justify="right", width=14)
    table.add_column("Cost (USD)", justify="right", width=12)

    for s in summaries:
        cost_str = f"${s.estimated_cost_usd:.6f}" if s.estimated_cost_usd is not None else "free"
        success_style = "green" if s.success_rate >= 0.8 else "yellow" if s.success_rate >= 0.5 else "red"
        table.add_row(
            s.model,
            f"{s.avg_time_seconds:.2f}",
            f"[{success_style}]{s.success_rate * 100:.1f}%[/{success_style}]",
            f"{s.avg_confidence:.3f}",
            cost_str,
        )

    console.print(table)

    # Show sample fields from best model
    best = max(summaries, key=lambda s: (s.success_rate, s.avg_confidence))
    if best.sample_fields:
        console.print(f"\n[dim]Best model: {best.model} — sample extraction:[/dim]")
        for k, v in best.sample_fields.items():
            console.print(f"  {k}: {v}")

    # Save to file if requested
    if output_path:
        results_json = benchmark_to_json(summaries)
        Path(output_path).write_text(json.dumps(results_json, indent=2), encoding="utf-8")
        console.print(f"\n[dim]Results saved to {output_path}[/dim]")


if __name__ == "__main__":
    cli()
