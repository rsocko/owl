"""CLI entry point for the Paperless Action Queue."""

import asyncio

import click
from rich.console import Console

console = Console()


@click.group()
def cli():
    """Paperless Action Queue — Extract actions from your documents."""
    from doc_intelligence_hub.core.logging_config import configure_logging
    configure_logging()


@cli.command()
@click.option("--force", is_flag=True, help="Re-analyze all documents, ignoring history")
@click.option("--limit", type=int, default=None, help="Max number of documents to process")
@click.option("--document-id", type=int, default=None, help="Analyze a single document by ID")
@click.option("--tag", type=str, default=None, help="Filter by tag name (default: Inbox,Todo from config)")
@click.option("--saved-view", type=int, default=None, help="Use a Paperless saved view ID as the source")
@click.option("--created-after", type=str, default=None, help="Only docs created after date (YYYY-MM-DD)")
@click.option("--created-before", type=str, default=None, help="Only docs created before date (YYYY-MM-DD)")
@click.option("--added-after", type=str, default=None, help="Only docs added after date (YYYY-MM-DD)")
@click.option("--added-before", type=str, default=None, help="Only docs added before date (YYYY-MM-DD)")
@click.option("--correspondent", type=str, default=None, help="Filter by correspondent name")
@click.option("--document-type", type=str, default=None, help="Filter by document type name")
@click.option("--dry-run", is_flag=True, help="Show what would be processed without calling Ollama")
def run(force, limit, document_id, tag, saved_view, created_after, created_before,
        added_after, added_before, correspondent, document_type, dry_run):
    """Run the analysis pipeline.

    Examples:\b
        paq run --limit 5                    # Process up to 5 docs from Inbox/Todo
        paq run --document-id 1234           # Analyze one specific document
        paq run --tag Inbox --limit 10       # Only Inbox tag, first 10
        paq run --saved-view 3               # Use a Paperless saved view
        paq run --added-after 2026-06-01     # Only recently added docs
        paq run --correspondent "PowerCo"    # Only docs from PowerCo
        paq run --dry-run                    # Preview without analyzing
        paq run --dry-run --tag Inbox        # See what's in Inbox
    """
    from .pipeline import run_pipeline
    asyncio.run(run_pipeline(
        force=force,
        limit=limit,
        document_id=document_id,
        tag_override=tag,
        saved_view_id=saved_view,
        created_after=created_after,
        created_before=created_before,
        added_after=added_after,
        added_before=added_before,
        correspondent=correspondent,
        document_type=document_type,
        dry_run=dry_run,
    ))


@cli.command()
def setup():
    """Set up custom fields in Paperless-NGX."""
    from .enricher import PaperlessEnricher

    async def _setup():
        enricher = PaperlessEnricher()
        console.print("[bold]Setting up Paperless custom fields...[/bold]\n")
        field_map = await enricher.ensure_custom_fields_exist()
        console.print(f"\n[green]✓ {len(field_map)} custom fields ready:[/green]")
        for name, field_id in field_map.items():
            console.print(f"  • {name} (id={field_id})")

    asyncio.run(_setup())


@cli.command()
def views():
    """List saved views from Paperless-NGX (use their IDs with --saved-view)."""
    from doc_intelligence_hub.core.paperless import PaperlessClient
    from .config import settings
    from rich.table import Table

    async def _views():
        client = PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_api_token)
        saved_views = await client.list_saved_views()
        if not saved_views:
            console.print("[yellow]No saved views found.[/yellow]")
            return

        table = Table(show_header=True, title="Paperless Saved Views")
        table.add_column("ID", style="bold")
        table.add_column("Name")
        table.add_column("# Rules")
        for view in saved_views:
            table.add_row(
                str(view["id"]),
                view.get("name", ""),
                str(len(view.get("filter_rules", []))),
            )
        console.print(table)
        console.print("\n[dim]Use: paq run --saved-view <ID>[/dim]")

    asyncio.run(_views())


@cli.command()
def check():
    """Verify connectivity to Paperless-NGX and Ollama."""
    from doc_intelligence_hub.core.paperless import PaperlessClient
    from .config import settings
    from .analyzer import OllamaAnalyzer

    async def _check():
        console.print("[bold]Connectivity Check[/bold]\n")

        client = PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_api_token)
        try:
            result = await client.health_check()
            ok = result.get("status") == "ok"
        except Exception:
            ok = False
        status = "[green]✓ Connected[/green]" if ok else "[red]✗ Failed[/red]"
        console.print(f"  Paperless-NGX: {status}")

        analyzer = OllamaAnalyzer()
        ok = await analyzer.health_check()
        status = "[green]✓ Connected[/green]" if ok else "[red]✗ Failed[/red]"
        console.print(f"  Ollama ({analyzer.model}): {status}")

    asyncio.run(_check())


@cli.command()
def sync():
    """Bidirectional sync: pull status changes from Paperless into the DB.

    If a user marks an action 'completed' or 'dismissed' directly in Paperless,
    this command detects the change and updates the internal database to match.
    Run this before viewing the dashboard to ensure it reflects manual Paperless edits.
    """
    from .database import get_session, init_db as _init_db, Action
    from .enricher import PaperlessEnricher
    from datetime import datetime

    async def _sync():
        _init_db()
        db = get_session()
        enricher = PaperlessEnricher()

        # Get all actions that have been synced at least once
        pending_actions = db.query(Action).filter(
            Action.status == "pending",
            Action.last_synced_status.isnot(None),
        ).all()

        if not pending_actions:
            console.print("[dim]No synced actions to check.[/dim]")
            db.close()
            return

        console.print(f"[dim]Checking {len(pending_actions)} actions for Paperless changes...[/dim]")
        updated = 0

        # Group by document_id (multiple actions per doc share one Paperless field)
        doc_ids = set(a.document_id for a in pending_actions)
        for doc_id in doc_ids:
            paperless_status = await enricher.read_paperless_status(doc_id)
            if not paperless_status:
                continue

            # If Paperless shows completed/dismissed but our DB says pending → user changed it
            if paperless_status in ("completed", "dismissed"):
                doc_actions = [a for a in pending_actions if a.document_id == doc_id]
                for action in doc_actions:
                    if action.status == "pending":
                        action.status = paperless_status
                        action.updated_at = datetime.utcnow()
                        if paperless_status == "completed":
                            action.completed_at = datetime.utcnow()
                        action.last_synced_status = paperless_status
                        updated += 1
                        console.print(
                            f"  [cyan]↓[/cyan] {action.title[:50]} → {paperless_status} (from Paperless)"
                        )

        db.commit()
        db.close()

        if updated:
            console.print(f"\n[green]✓ Synced {updated} status changes from Paperless[/green]")
        else:
            console.print("[dim]Everything in sync.[/dim]")

    asyncio.run(_sync())


@cli.command()
def init_db():
    """Initialize the SQLite database."""
    from .database import init_db as _init_db
    _init_db()
    console.print("[green]✓ Database initialized[/green]")


@cli.command()
def status():
    """Show current action queue status."""
    from .database import get_session, Action, init_db as _init_db
    from rich.table import Table

    _init_db()
    db = get_session()

    pending = db.query(Action).filter_by(status="pending").count()
    completed = db.query(Action).filter_by(status="completed").count()
    dismissed = db.query(Action).filter_by(status="dismissed").count()

    console.print("[bold]Action Queue Status[/bold]\n")
    console.print(f"  Pending:   {pending}")
    console.print(f"  Completed: {completed}")
    console.print(f"  Dismissed: {dismissed}")
    console.print(f"  Total:     {pending + completed + dismissed}")

    # Show urgent items
    urgent = (
        db.query(Action)
        .filter(Action.status == "pending", Action.urgency.in_(["CRITICAL", "HIGH"]))
        .order_by(Action.due_date)
        .all()
    )

    if urgent:
        console.print(f"\n[bold red]⚠ Urgent Actions ({len(urgent)}):[/bold red]")
        table = Table(show_header=True)
        table.add_column("Type", style="bold")
        table.add_column("Title")
        table.add_column("Due")
        table.add_column("Amount")
        table.add_column("Urgency")

        for a in urgent[:10]:
            table.add_row(
                a.action_type,
                a.title[:50],
                str(a.due_date) if a.due_date else "—",
                f"${a.amount:.2f}" if a.amount else "—",
                a.urgency,
            )
        console.print(table)

    db.close()


if __name__ == "__main__":
    cli()
