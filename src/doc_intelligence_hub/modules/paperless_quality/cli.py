"""Privacy-safe CLI for Paperless quality saved views and Manual corrections."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

import click
import httpx

from doc_intelligence_hub.core.paperless import PaperlessClient
from doc_intelligence_hub.core.resilience import PaperlessError
from doc_intelligence_hub.modules.metadata_migration.state import SQLiteMigrationStateStore

from .config import load_quality_config
from .service import PaperlessQualityService, load_protected_plan


def _run(coroutine) -> tuple[str, int]:
    try:
        return asyncio.run(coroutine)
    except (ValueError, PermissionError) as exc:
        raise click.ClickException(str(exc)) from exc
    except (PaperlessError, httpx.HTTPError) as exc:
        raise click.ClickException("Paperless quality operation failed closed.") from exc


def _client(url: str, token: str) -> PaperlessClient:
    return PaperlessClient(base_url=url, token=token)


def _connection_options(function):
    function = click.option(
        "--paperless-token",
        envvar="PAPERLESS_API_TOKEN",
        required=True,
        help="Paperless API token (never emitted).",
    )(function)
    return click.option(
        "--paperless-url",
        envvar="PAPERLESS_URL",
        required=True,
        help="Paperless base URL (never emitted).",
    )(function)


def _require_apply_gates(
    *,
    external_writers_disabled: bool,
    state_db: str,
    backup_verified_at: tuple[str, ...] = (),
) -> None:
    if not external_writers_disabled:
        raise click.UsageError("--apply requires --external-writers-disabled")
    if os.getenv("WRITE_TO_PAPERLESS", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise click.UsageError("--apply refused while WRITE_TO_PAPERLESS is enabled")
    if not state_db:
        raise click.UsageError("--apply requires protected --state-db")
    now = datetime.now(UTC)
    for value in backup_verified_at:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise click.UsageError("Backup verification timestamps must be RFC 3339") from exc
        age = now - timestamp.astimezone(UTC) if timestamp.tzinfo is not None else None
        if age is None or age.total_seconds() < 0 or age > __import__(
            "datetime"
        ).timedelta(hours=24):
            raise click.UsageError("Backup verification must be timezone-aware and under 24h old")


@click.group()
def cli() -> None:
    """Provision Paperless quality views and guarded Manual corrections."""


@cli.command()
@_connection_options
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--protected-output", type=click.Path(dir_okay=False), required=True)
def plan(
    paperless_url: str,
    paperless_token: str,
    config_path: str,
    protected_output: str,
) -> None:
    """GET-only inventory with redacted counts and an immutable plan digest."""
    config = load_quality_config(config_path)

    async def run() -> tuple[str, int]:
        async with _client(paperless_url, paperless_token) as client:
            summary = await PaperlessQualityService(client, config).plan(
                protected_output=protected_output
            )
            return summary.to_json(), 2 if summary.completion_state == "review_required" else 0

    output, status = _run(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command("apply-views")
@_connection_options
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--plan", "plan_path", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--plan-digest", required=True)
@click.option("--approval", required=True)
@click.option("--state-db", type=click.Path(dir_okay=False), required=True)
@click.option("--external-writers-disabled", is_flag=True)
@click.option("--apply", is_flag=True, required=True)
def apply_views(
    paperless_url: str,
    paperless_token: str,
    config_path: str,
    plan_path: str,
    plan_digest: str,
    approval: str,
    state_db: str,
    external_writers_disabled: bool,
    apply: bool,
) -> None:
    """Create or update managed views only after exact-plan approval."""
    _require_apply_gates(
        external_writers_disabled=external_writers_disabled,
        state_db=state_db,
    )
    protected_plan = load_protected_plan(plan_path)
    if protected_plan.plan_digest != plan_digest:
        raise click.UsageError("--plan-digest does not match the protected plan")
    config = load_quality_config(config_path)

    async def run() -> tuple[str, int]:
        store = SQLiteMigrationStateStore(state_db)
        async with _client(paperless_url, paperless_token) as client:
            summary = await PaperlessQualityService(client, config).apply_views(
                protected_plan,
                approval=approval,
                state_store=store,
            )
            return summary.to_json(), 2 if summary.completion_state == "review_required" else 0

    output, status = _run(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command("apply-manual-storage-path")
@_connection_options
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--plan", "plan_path", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--plan-digest", required=True)
@click.option("--approval", required=True)
@click.option("--state-db", type=click.Path(dir_okay=False), required=True)
@click.option("--batch-size", type=click.IntRange(min=1, max=100), default=25)
@click.option("--external-writers-disabled", is_flag=True)
@click.option("--manufacturer-reviewed", is_flag=True)
@click.option("--paperless-export-verified-at", required=True)
@click.option("--owl-backup-verified-at", required=True)
@click.option("--apply", is_flag=True, required=True)
def apply_manual_storage_path(
    paperless_url: str,
    paperless_token: str,
    config_path: str,
    plan_path: str,
    plan_digest: str,
    approval: str,
    state_db: str,
    batch_size: int,
    external_writers_disabled: bool,
    manufacturer_reviewed: bool,
    paperless_export_verified_at: str,
    owl_backup_verified_at: str,
    apply: bool,
) -> None:
    """Correct only planned Manuals after all fail-closed gates pass."""
    if not manufacturer_reviewed:
        raise click.UsageError("--apply requires --manufacturer-reviewed")
    _require_apply_gates(
        external_writers_disabled=external_writers_disabled,
        state_db=state_db,
        backup_verified_at=(paperless_export_verified_at, owl_backup_verified_at),
    )
    protected_plan = load_protected_plan(plan_path)
    if protected_plan.plan_digest != plan_digest:
        raise click.UsageError("--plan-digest does not match the protected plan")
    config = load_quality_config(config_path)

    async def run() -> tuple[str, int]:
        store = SQLiteMigrationStateStore(state_db)
        async with _client(paperless_url, paperless_token) as client:
            summary = await PaperlessQualityService(client, config).apply_manual_storage_path(
                protected_plan,
                approval=approval,
                state_store=store,
                batch_size=batch_size,
            )
            status = 1 if summary.completion_state == "failed" else (
                2 if summary.completion_state in {"review_required", "partial"} else 0
            )
            return summary.to_json(), status

    output, status = _run(run())
    click.echo(output)
    raise SystemExit(status)


if __name__ == "__main__":
    cli()
