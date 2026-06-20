"""CLI for EOB Matching — fetch from Paperless, classify, extract, match."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.modules.eob_matching.classifier import classify_document
from doc_intelligence_hub.modules.eob_matching.extractor import extract_eob, extract_bill
from doc_intelligence_hub.modules.eob_matching.matcher import match_documents
from doc_intelligence_hub.modules.eob_matching.models import DocumentType, MatchConfidence

console = Console()


@click.group()
def cli():
    """EOB Matching — classify, extract, and match medical documents."""
    pass


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True, help="Paperless-ngx URL")
@click.option("--paperless-token", envvar="PAPERLESS_TOKEN", required=True, help="Paperless API token")
@click.option("--tag", multiple=True, help="Filter by tag name (can specify multiple)")
@click.option("--correspondent", type=str, default=None, help="Filter by correspondent name")
@click.option("--limit", type=int, default=50, help="Max documents to process")
@click.option("--output", type=click.Path(), default=None, help="Save results to JSON file")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed extraction results")
def run(paperless_url, paperless_token, tag, correspondent, limit, output, verbose):
    """Run the full pipeline: fetch → classify → extract → match.

    This is a DRY RUN by default — it reads documents and shows results
    but never writes anything back to Paperless.

    Examples:\b
        eob-match run --tag medical --limit 20
        eob-match run --correspondent "UnitedHealthcare" --verbose
        eob-match run --tag medical-eob --tag medical-bill --output results.json
    """
    asyncio.run(_run_pipeline(
        paperless_url=paperless_url,
        paperless_token=paperless_token,
        tags=list(tag) if tag else None,
        correspondent=correspondent,
        limit=limit,
        output_path=output,
        verbose=verbose,
    ))


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True)
@click.option("--paperless-token", envvar="PAPERLESS_TOKEN", required=True)
@click.option("--tag", multiple=True, help="Filter by tag name")
@click.option("--limit", type=int, default=20, help="Max documents to scan")
def classify(paperless_url, paperless_token, tag, limit):
    """Classify documents as EOB, Bill, or Unknown (read-only).

    Fetches documents and shows classification results without modifying anything.

    Examples:\b
        eob-match classify --tag medical --limit 10
        eob-match classify --limit 50
    """
    asyncio.run(_classify_only(
        paperless_url=paperless_url,
        paperless_token=paperless_token,
        tags=list(tag) if tag else None,
        limit=limit,
    ))


@cli.command()
@click.option("--paperless-url", envvar="PAPERLESS_URL", required=True)
@click.option("--paperless-token", envvar="PAPERLESS_TOKEN", required=True)
def check(paperless_url, paperless_token):
    """Verify connectivity to Paperless-ngx."""
    asyncio.run(_check_connection(paperless_url, paperless_token))


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


async def _classify_only(paperless_url: str, paperless_token: str, tags: list[str] | None, limit: int):
    """Fetch and classify documents."""
    client = PaperlessClient(base_url=paperless_url, token=paperless_token)

    console.print("[bold]EOB Matching — Document Classification[/bold]\n")
    console.print(f"[dim]Fetching up to {limit} documents...[/dim]")

    documents = await client.list_documents(tags=tags, page_size=min(limit, 100))
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
    limit: int,
    output_path: str | None,
    verbose: bool,
):
    """Full pipeline: fetch → classify → extract → match."""
    client = PaperlessClient(base_url=paperless_url, token=paperless_token)

    console.print(Panel("[bold]EOB Matching Pipeline[/bold] (read-only / dry-run)", style="blue"))
    console.print()

    # Step 1: Fetch documents
    console.print("[bold]Step 1:[/bold] Fetching documents...")
    documents = await client.list_documents(tags=tags, correspondent=correspondent, page_size=min(limit, 100))
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
            eob_docs.append(doc)
        elif result.type == DocumentType.BILL:
            bill_docs.append(doc)
        else:
            unknown_docs.append(doc)

    console.print(f"  [cyan]EOBs:[/cyan] {len(eob_docs)} | [yellow]Bills:[/yellow] {len(bill_docs)} | [dim]Unknown:[/dim] {len(unknown_docs)}\n")

    if not eob_docs and not bill_docs:
        console.print("[yellow]No medical documents classified. Try broader filters or more documents.[/yellow]")
        return

    # Step 3: Extract
    console.print("[bold]Step 3:[/bold] Extracting structured data...")
    extracted_eobs = []
    extracted_bills = []

    for doc in eob_docs:
        content = doc.get("content", "")
        extracted = extract_eob(content, document_id=str(doc["id"]))
        extracted_eobs.append(extracted)
        if verbose:
            console.print(f"  [cyan]EOB #{doc['id']}[/cyan]: provider={extracted.provider_name or '?'}, "
                         f"patient={extracted.patient_name or '?'}, "
                         f"date={extracted.date_of_service or '?'}, "
                         f"patient_resp=${extracted.total_patient_responsibility or 0:.2f}")

    for doc in bill_docs:
        content = doc.get("content", "")
        extracted = extract_bill(content, document_id=str(doc["id"]))
        extracted_bills.append(extracted)
        if verbose:
            console.print(f"  [yellow]Bill #{doc['id']}[/yellow]: provider={extracted.provider_name or '?'}, "
                         f"patient={extracted.patient_name or '?'}, "
                         f"date={extracted.date_of_service or '?'}, "
                         f"balance=${extracted.balance_due or 0:.2f}")

    console.print(f"  [green]✓[/green] Extracted {len(extracted_eobs)} EOBs, {len(extracted_bills)} bills\n")

    # Step 4: Match
    console.print("[bold]Step 4:[/bold] Running matching engine...")
    matches = match_documents(extracted_eobs, extracted_bills)

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

    # Step 5: Summary
    console.print()
    high = sum(1 for m in matches if m.confidence == MatchConfidence.HIGH)
    medium = sum(1 for m in matches if m.confidence == MatchConfidence.MEDIUM)
    low = sum(1 for m in matches if m.confidence == MatchConfidence.LOW)
    unmatched_eobs = len(extracted_eobs) - len(set(m.eob_id for m in matches))
    unmatched_bills = len(extracted_bills) - len(set(m.bill_id for m in matches))

    console.print(Panel(
        f"[bold]Summary[/bold]\n"
        f"  Documents scanned: {len(documents)}\n"
        f"  Classified: {len(eob_docs)} EOBs, {len(bill_docs)} bills, {len(unknown_docs)} unknown\n"
        f"  Matches: {high} high, {medium} medium, {low} low confidence\n"
        f"  Unmatched: {unmatched_eobs} EOBs, {unmatched_bills} bills waiting",
        style="green" if matches else "yellow",
    ))

    # Save output if requested
    if output_path:
        output_data = {
            "run_at": datetime.now().isoformat(),
            "documents_scanned": len(documents),
            "classifications": {
                "eob": [{"id": d["id"], "title": d.get("title")} for d in eob_docs],
                "bill": [{"id": d["id"], "title": d.get("title")} for d in bill_docs],
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


if __name__ == "__main__":
    cli()
