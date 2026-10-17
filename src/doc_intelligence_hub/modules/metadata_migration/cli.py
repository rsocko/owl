"""Command-line interface for safe Paperless metadata inventory and migration."""

from __future__ import annotations

import asyncio
import json
import os

import click
import httpx
from sqlalchemy.exc import SQLAlchemyError

from doc_intelligence_hub.core.paperless import MetadataFieldKey, PaperlessClient
from doc_intelligence_hub.core.resilience import PaperlessError

from .service import MetadataMigrationService
from .state import SQLiteMigrationStateStore


def _client(url: str, token: str) -> PaperlessClient:
    return PaperlessClient(base_url=url, token=token)


def _run_command(coroutine) -> tuple[str, int]:
    try:
        return asyncio.run(coroutine)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    except (PaperlessError, SQLAlchemyError, httpx.HTTPError) as exc:
        raise click.ClickException("Metadata operation failed without a success result.") from exc


def _require_apply_preflight(apply: bool, external_writers_disabled: bool) -> None:
    if not apply:
        return
    if not external_writers_disabled:
        raise click.UsageError(
            "--apply requires --external-writers-disabled to confirm a single writer"
        )
    if os.getenv("WRITE_TO_PAPERLESS", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise click.UsageError(
            "--apply refused while WRITE_TO_PAPERLESS enables another OWL write path"
        )


def _owl_retirement_coverage() -> dict[MetadataFieldKey, set[int]]:
    from doc_intelligence_hub.modules.action_queue.database import Action
    from doc_intelligence_hub.modules.action_queue.database import (
        get_session as get_action_session,
    )
    from doc_intelligence_hub.modules.eob_matching.database import (
        BillRecord,
        EOBRecord,
        MatchRecord,
    )
    from doc_intelligence_hub.modules.eob_matching.database import (
        get_session as get_eob_session,
    )

    action_session = get_action_session()
    try:
        action_document_ids = {
            int(row[0]) for row in action_session.query(Action.document_id).distinct().all()
        }
    finally:
        action_session.close()

    eob_session = get_eob_session()
    try:
        eob_document_ids = {
            int(row[0]) for row in eob_session.query(EOBRecord.document_id).distinct().all()
        }
        bill_document_ids = {
            int(row[0]) for row in eob_session.query(BillRecord.document_id).distinct().all()
        }
        responsibility_document_ids = {
            int(row[0])
            for row in eob_session.query(EOBRecord.document_id)
            .filter(EOBRecord.total_patient_responsibility.is_not(None))
            .distinct()
            .all()
        }
        match_rows = eob_session.query(
            MatchRecord.eob_document_id,
            MatchRecord.bill_document_id,
        ).all()
    finally:
        eob_session.close()

    classified_document_ids = eob_document_ids | bill_document_ids
    matched_document_ids = {
        int(document_id) for row in match_rows for document_id in row if document_id is not None
    }
    coverage: dict[MetadataFieldKey, set[int]] = {}
    for key in (
        MetadataFieldKey.ACTION_ANALYZED,
        MetadataFieldKey.LEGACY_ACTION_TYPE,
        MetadataFieldKey.LEGACY_ACTION_DUE_DATE,
        MetadataFieldKey.LEGACY_ACTION_URGENCY,
        MetadataFieldKey.LEGACY_ACTION_SUMMARY,
        MetadataFieldKey.LEGACY_ACTION_COUNT,
    ):
        coverage[key] = action_document_ids
    for key in (
        MetadataFieldKey.EOB_MATCH_STATUS,
        MetadataFieldKey.EOB_DOCUMENT_TYPE,
        MetadataFieldKey.EOB_ANALYZED,
    ):
        coverage[key] = classified_document_ids
    for key in (
        MetadataFieldKey.EOB_MATCH_SCORE,
        MetadataFieldKey.EOB_MATCH_CONFIDENCE,
        MetadataFieldKey.EOB_MATCHED_DOCUMENT,
    ):
        coverage[key] = matched_document_ids
    coverage[MetadataFieldKey.EOB_PATIENT_RESPONSIBILITY] = responsibility_document_ids
    return coverage


@click.group()
def cli() -> None:
    """Inventory and backfill canonical Paperless metadata safely."""


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


@cli.command()
@_connection_options
@click.option("--batch-size", type=click.IntRange(min=1, max=1000), default=100)
@click.option(
    "--protected-output",
    type=click.Path(dir_okay=False),
    help="Explicit protected path for detailed IDs, definitions, and audit records.",
)
def inventory(
    paperless_url: str,
    paperless_token: str,
    batch_size: int,
    protected_output: str | None,
) -> None:
    """Run a GET-only inventory and emit a redacted summary."""

    async def run() -> tuple[str, int]:
        async with _client(paperless_url, paperless_token) as client:
            summary = await MetadataMigrationService(client).inventory(
                batch_size=batch_size,
                protected_output=protected_output,
            )
            return summary.to_json(), summary.exit_status

    output, status = _run_command(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command()
@_connection_options
@click.option("--apply", is_flag=True, help="Create eligible missing canonical fields.")
@click.option(
    "--external-writers-disabled",
    is_flag=True,
    help="Confirm Paperless-AI and other metadata writers are disabled.",
)
def prepare(
    paperless_url: str,
    paperless_token: str,
    apply: bool,
    external_writers_disabled: bool,
) -> None:
    """Propose canonical fields; mutate only with explicit apply preflight."""
    _require_apply_preflight(apply, external_writers_disabled)

    async def run() -> tuple[str, int]:
        async with _client(paperless_url, paperless_token) as client:
            summary = await MetadataMigrationService(client).prepare(apply=apply)
            return summary.to_json(), summary.exit_status

    output, status = _run_command(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command("field-audit")
@_connection_options
@click.option("--batch-size", type=click.IntRange(min=1, max=1000), default=100)
@click.option(
    "--protected-output",
    type=click.Path(dir_okay=False),
    help="Protected path for deployed field names, IDs, and dependency details.",
)
def field_audit(
    paperless_url: str,
    paperless_token: str,
    batch_size: int,
    protected_output: str | None,
) -> None:
    """Review all managed fields and count unmanaged fields without changing Paperless."""

    async def run() -> tuple[str, int]:
        async with _client(paperless_url, paperless_token) as client:
            summary = await MetadataMigrationService(
                client,
                owl_document_coverage=_owl_retirement_coverage(),
            ).review_fields(
                batch_size=batch_size,
                protected_output=protected_output,
            )
            return summary.to_json(), summary.exit_status

    output, status = _run_command(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command("retire-fields")
@_connection_options
@click.option("--apply", is_flag=True, help="Clear covered values and delete eligible fields.")
@click.option(
    "--discard-retired-values",
    is_flag=True,
    help="Clear populated registry-approved retired fields before deleting them.",
)
@click.option(
    "--external-writers-disabled",
    is_flag=True,
    help="Confirm Paperless-AI and other metadata writers are disabled.",
)
@click.option("--batch-size", type=click.IntRange(min=1, max=1000), default=100)
@click.option(
    "--protected-output",
    type=click.Path(dir_okay=False),
    help="Protected path for deployed field names, IDs, and dependency details.",
)
def retire_fields(
    paperless_url: str,
    paperless_token: str,
    apply: bool,
    discard_retired_values: bool,
    external_writers_disabled: bool,
    batch_size: int,
    protected_output: str | None,
) -> None:
    """Dry-run by default; retire only registry-approved dependency-free fields."""
    _require_apply_preflight(apply, external_writers_disabled)
    if apply and not protected_output:
        raise click.UsageError("--apply requires --protected-output")
    if discard_retired_values and not apply:
        raise click.UsageError("--discard-retired-values requires --apply")

    async def run() -> tuple[str, int]:
        async with _client(paperless_url, paperless_token) as client:
            summary = await MetadataMigrationService(
                client,
                owl_document_coverage=_owl_retirement_coverage(),
            ).review_fields(
                apply=apply,
                discard_retired_values=discard_retired_values,
                batch_size=batch_size,
                protected_output=protected_output,
            )
            return summary.to_json(), summary.exit_status

    output, status = _run_command(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command()
@_connection_options
@click.option("--apply", is_flag=True, help="Apply canonical-only document updates.")
@click.option(
    "--external-writers-disabled",
    is_flag=True,
    help="Confirm Paperless-AI and other metadata writers are disabled.",
)
@click.option("--batch-size", type=click.IntRange(min=1, max=1000), default=100)
@click.option("--max-retries", type=click.IntRange(min=0, max=10), default=2)
@click.option("--state-db", type=click.Path(dir_okay=False))
@click.option("--run-id", type=str)
@click.option("--resume", is_flag=True)
def backfill(
    paperless_url: str,
    paperless_token: str,
    apply: bool,
    external_writers_disabled: bool,
    batch_size: int,
    max_retries: int,
    state_db: str | None,
    run_id: str | None,
    resume: bool,
) -> None:
    """Dry-run by default; apply in bounded, restartable batches."""
    _require_apply_preflight(apply, external_writers_disabled)
    if apply and not state_db:
        raise click.UsageError("--apply requires --state-db in protected runtime storage")
    if resume and (not apply or not run_id):
        raise click.UsageError("--resume requires --apply and --run-id")

    async def run() -> tuple[str, int]:
        store = SQLiteMigrationStateStore(state_db) if state_db else None
        async with _client(paperless_url, paperless_token) as client:
            summary = await MetadataMigrationService(client).backfill(
                apply=apply,
                batch_size=batch_size,
                max_retries=max_retries,
                state_store=store,
                run_id=run_id,
                resume=resume,
            )
            return summary.to_json(), summary.exit_status

    output, status = _run_command(run())
    click.echo(output)
    raise SystemExit(status)


@cli.command()
@click.option("--state-db", type=click.Path(exists=True, dir_okay=False), required=True)
@click.option("--run-id", type=str, required=True)
def report(state_db: str, run_id: str) -> None:
    """Render aggregate counts without exposing protected audit rows."""
    store = SQLiteMigrationStateStore(state_db)
    totals, grouped = store.sanitized_counts(run_id)
    click.echo(
        json.dumps(
            {
                "schema_version": "1.0",
                "run_id": run_id,
                "counts": totals,
                "counts_by_key": grouped,
                "redacted": True,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    cli()
