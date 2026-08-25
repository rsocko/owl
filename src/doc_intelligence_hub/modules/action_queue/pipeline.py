"""Main processing pipeline — orchestrates fetch → analyze → store → enrich."""

import asyncio
import io
import logging
import time
from datetime import date, datetime
from typing import Any

import httpx
from rich.console import Console

from doc_intelligence_hub.core.extractors.account_numbers import (
    extract_account_numbers,
    pick_masked_account_identifier,
)
from doc_intelligence_hub.core.paperless import PaperlessClient

from .analyzer import OllamaAnalyzer, urgency_to_severity
from .config import settings
from .database import Action, ProcessingHistory, get_session, init_db
from .enricher import PaperlessEnricher
from .fallback_analyzer import RuleBasedAnalyzer
from .lifecycle import action_has_critical_details, mark_action_ready, route_action_to_review
from .risk_scoring import compute_risk_score

logger = logging.getLogger(__name__)

# Use a file-based console to avoid Windows encoding issues when running under uvicorn
console = Console(file=io.StringIO(), force_terminal=False, highlight=False)


def _serialize_cta(cta: dict | str | None) -> str | None:
    """Serialize a CTA dict to a JSON string for database storage, or None."""
    import json

    if cta is None:
        return None
    if isinstance(cta, str):
        return cta
    if isinstance(cta, dict):
        return json.dumps(cta)
    return None


# ---------------------------------------------------------------------------
# Progress tracking — read by the /api/queue/status endpoint while a run is
# in flight. Since the pipeline runs on the same asyncio event loop as the
# API server, other coroutines (like a concurrent status request) can read
# this module-level state at any `await` point during the run.
# ---------------------------------------------------------------------------
_progress: dict[str, Any] = {"current_step": "idle", "progress": None, "current_document": None}
_progress_start: float | None = None
_pipeline_run_lock = asyncio.Lock()
_last_pipeline_start: float | None = None


def get_pipeline_progress() -> dict[str, Any]:
    """Return a snapshot of the current/last pipeline run's progress."""
    snapshot = dict(_progress)
    if _progress_start is not None and snapshot.get("current_step") not in (None, "idle"):
        snapshot["elapsed_seconds"] = round(time.monotonic() - _progress_start, 1)
    else:
        snapshot["elapsed_seconds"] = None
    return snapshot


def _reset_progress() -> None:
    global _progress_start
    _progress.clear()
    _progress.update({"current_step": "starting", "progress": None, "current_document": None})
    _progress_start = time.monotonic()


def _set_progress(**kwargs: Any) -> None:
    _progress.update(kwargs)


def _make_paperless_client() -> PaperlessClient:
    """Create a PaperlessClient from action queue settings."""
    return PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_api_token)


class Pipeline:
    """Orchestrates the document analysis pipeline."""

    def __init__(self):
        self.paperless = _make_paperless_client()
        self.analyzer = OllamaAnalyzer()
        self.fallback_analyzer = RuleBasedAnalyzer()
        self.enricher = PaperlessEnricher()
        self._ollama_available: bool | None = None
        self._enrichment_available: bool = True

    async def run(
        self,
        force: bool = False,
        limit: int | None = None,
        document_id: int | None = None,
        tag_override: str | None = None,
        saved_view_id: int | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        added_after: str | None = None,
        added_before: str | None = None,
        correspondent: str | None = None,
        document_type: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Execute a full pipeline run.

        Args:
            force: Re-analyze documents even if already processed.
            limit: Max number of documents to process.
            document_id: Analyze only this specific document.
            tag_override: Override the configured tags to scan.
            saved_view_id: Use a Paperless saved view as document source.
            created_after: Filter by document creation date (YYYY-MM-DD).
            created_before: Filter by document creation date (YYYY-MM-DD).
            added_after: Filter by date added to Paperless (YYYY-MM-DD).
            added_before: Filter by date added to Paperless (YYYY-MM-DD).
            correspondent: Filter by correspondent name.
            document_type: Filter by document type name.
            dry_run: List documents but don't call Ollama.

        Returns:
            Summary dict with counts of processed/skipped/failed documents.
        """
        console.print("\n[bold blue]═══ Paperless Action Queue Pipeline ═══[/bold blue]\n")

        run_start = time.monotonic()
        _reset_progress()
        _set_progress(current_step="fetching_documents")
        logger.info(
            "Pipeline run starting: limit=%s dry_run=%s force=%s read_only=%s document_id=%s",
            limit,
            dry_run,
            force,
            not settings.write_to_paperless,
            document_id,
        )

        # Build a correspondent ID→name cache
        self._correspondent_cache: dict[int, str] = {}
        try:
            correspondents = await self.paperless.list_correspondents()
            self._correspondent_cache = {c["id"]: c["name"] for c in correspondents}
        except (httpx.TimeoutException, httpx.ConnectError, OSError) as exc:
            logger.info("Could not load correspondent cache (will use IDs): %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error loading correspondent cache: %s", exc)

        # Build tag and document type ID→name caches
        self._tag_cache: dict[int, str] = {}
        self._doc_type_cache: dict[int, str] = {}
        self._tag_cache: dict[int, str] = {}
        try:
            _, tags, doc_types = await self.paperless.fetch_all_metadata()
            self._tag_cache = tags
            self._doc_type_cache = doc_types
        except (httpx.TimeoutException, httpx.ConnectError, OSError) as exc:
            logger.info("Could not load tag and document type caches (will use IDs): %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error loading tag and document type caches: %s", exc)

        # Step 1: Ensure custom fields exist (skip in dry-run or read-only mode)
        self._enrichment_available = True
        if not dry_run and settings.write_to_paperless:
            console.print("[dim]Checking custom fields...[/dim]")
            try:
                await self.enricher.ensure_custom_fields_exist()
                console.print("[green]✓[/green] Custom fields ready\n")
                logger.info("Custom fields verified/created in Paperless")
            except Exception as e:
                console.print(
                    f"[yellow]⚠[/yellow] Custom fields check failed: {e}\n"
                    "[yellow]  Pipeline will continue — actions stored locally but not written to Paperless[/yellow]\n"
                )
                logger.warning(
                    "Custom fields check failed: %s — writes to Paperless disabled for this run", e
                )
                self._enrichment_available = False

        # Step 2: Fetch documents based on provided filters.
        # When a `limit` is given we push it down to the Paperless query so we
        # don't walk the full (potentially thousands-strong) result set just to
        # keep a handful of documents. When `force` is False we still need to
        # filter out already-processed documents afterwards, so we fetch a
        # small multiple of `limit` as a buffer rather than an unbounded set —
        # a large buffer (e.g. limit*10) defeats the point of a small limit
        # since it still has to be requested/paginated as a single page.
        fetch_limit: int | None = None
        if limit is not None and not document_id:
            fetch_limit = limit if force else limit * 3

        fetch_start = time.monotonic()
        try:
            documents = await asyncio.wait_for(
                self._fetch_documents(
                    document_id=document_id,
                    saved_view_id=saved_view_id,
                    tag_override=tag_override,
                    created_after=created_after,
                    created_before=created_before,
                    added_after=added_after,
                    added_before=added_before,
                    correspondent=correspondent,
                    document_type=document_type,
                    fetch_limit=fetch_limit,
                ),
                timeout=settings.pipeline_fetch_timeout_seconds,
            )
        except TimeoutError:
            fetch_duration = time.monotonic() - fetch_start
            console.print(
                f"[red]✗[/red] Document fetch timed out after {settings.pipeline_fetch_timeout_seconds:.0f}s"
            )
            logger.error(
                "Document fetch exceeded fetch timeout (%.0fs) — aborting run after %.2fs",
                settings.pipeline_fetch_timeout_seconds,
                fetch_duration,
            )
            _set_progress(
                current_step="complete", progress="0/0 documents processed (fetch timed out)"
            )
            return {
                "processed": 0,
                "skipped": 0,
                "failed": 0,
                "fetch_timed_out": True,
                "duration_seconds": round(time.monotonic() - run_start, 2),
            }

        fetch_duration = time.monotonic() - fetch_start
        console.print(f"[green]✓[/green] Found {len(documents)} documents")
        logger.info(
            "Document fetch: found %d documents matching filters (fetch_limit=%s, duration=%.2fs)",
            len(documents),
            fetch_limit,
            fetch_duration,
        )

        console.print()

        if not documents:
            console.print("[yellow]No documents to process.[/yellow]")
            _set_progress(current_step="complete", progress="0/0 documents processed")
            return {"processed": 0, "skipped": 0, "failed": 0}

        # Filter out already-processed documents (unless force re-scan)
        skipped_count = 0
        if not force:
            init_db()
            db_check = get_session()
            try:
                processed_ids = {
                    row.document_id
                    for row in db_check.query(ProcessingHistory.document_id)
                    .filter(ProcessingHistory.success == 1)
                    .all()
                }
            finally:
                db_check.close()
            unprocessed = [d for d in documents if d["id"] not in processed_ids]
            skipped_count = len(documents) - len(unprocessed)
            documents = unprocessed
            console.print(f"[dim]  {skipped_count} already processed, {len(documents)} new[/dim]")
            logger.info(
                "Document filter: %d already processed (skipped), %d new to analyze",
                skipped_count,
                len(documents),
            )

        # Apply limit AFTER filtering (limit means "analyze up to N new docs")
        if limit and len(documents) > limit:
            documents = documents[:limit]
            console.print(f"[dim]  (limited to {limit})[/dim]")
            logger.info("Document limit applied: analyzing %d of the new documents", limit)

        if not documents:
            console.print("[yellow]No new documents to process.[/yellow]")
            _set_progress(current_step="complete", progress="0/0 documents processed")
            return {
                "processed": 0,
                "skipped": skipped_count if not force else 0,
                "failed": 0,
                "no_action": 0,
            }

        # Dry-run: just list what would be processed
        if dry_run:
            console.print("[bold yellow]DRY RUN — no analysis will be performed[/bold yellow]\n")
            from rich.table import Table

            table = Table(show_header=True)
            table.add_column("ID", style="dim")
            table.add_column("Title")
            table.add_column("Tags")
            table.add_column("Added")
            for doc in documents:
                table.add_row(
                    str(doc["id"]),
                    doc.get("title", "")[:60],
                    ", ".join(str(t) for t in doc.get("tag_names", doc.get("tags", []))),
                    doc.get("added", "")[:10],
                )
            console.print(table)
            console.print(f"\n[dim]{len(documents)} documents would be processed.[/dim]")
            logger.info(
                "Dry run: %d documents would be processed (no analysis performed)", len(documents)
            )
            _set_progress(
                current_step="complete",
                progress=f"0/{len(documents)} documents processed (dry run)",
            )
            return {"processed": 0, "skipped": 0, "failed": 0, "would_process": len(documents)}

        # Step 3: Process each document
        init_db()
        db = get_session()
        stats: dict[str, Any] = {
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "no_action": 0,
            "enrichment_failed": 0,
            "errors": [],
            "timed_out": False,
        }
        max_duration = settings.pipeline_max_duration_seconds
        total_docs = len(documents)

        _set_progress(current_step="analyzing", progress=f"0/{total_docs} documents processed")
        logger.info(
            "Beginning analysis of %d documents (pipeline timeout=%.0fs)", total_docs, max_duration
        )

        for index, doc in enumerate(documents):
            doc_id = doc["id"]
            doc_title = doc.get("title", f"Document #{doc_id}")
            doc_title_short = doc_title[:50]

            # Overall pipeline timeout — stop processing remaining documents.
            elapsed = time.monotonic() - run_start
            if elapsed > max_duration:
                remaining = total_docs - index
                logger.warning(
                    "Pipeline max duration exceeded (%.0fs > %.0fs limit) — stopping with %d/%d "
                    "documents processed, %d document(s) skipped due to timeout",
                    elapsed,
                    max_duration,
                    index,
                    total_docs,
                    remaining,
                )
                stats["timed_out"] = True
                stats["skipped"] += remaining
                break

            _set_progress(
                current_step="analyzing",
                progress=f"{index}/{total_docs} documents processed",
                current_document=doc_title_short,
            )

            try:
                console.print(f"  [dim]Analyzing:[/dim] {doc_title[:60]}...")
                logger.info(
                    "Processing document: doc_id=%s title=%r (%d/%d)",
                    doc_id,
                    doc_title_short,
                    index + 1,
                    total_docs,
                )

                # Fetch full content if not already present
                if "content" not in doc or not doc["content"]:
                    doc["content"] = await self.paperless.get_document_content(doc_id)
                self._resolve_document_metadata(doc)

                # Compute basic text quality metrics
                content = doc.get("content", "")
                text_metrics = self._compute_text_quality(content)

                # Check if content is too short/empty to analyze
                if text_metrics["content_length"] < 20:
                    console.print(
                        "  [yellow]⚠[/yellow] No/minimal text content — marking unreadable"
                    )
                    logger.info("doc_id=%s: minimal/no text content — marking unreadable", doc_id)
                    self._record_history(
                        db,
                        doc_id,
                        success=True,
                        disposition="unreadable",
                        text_metrics=text_metrics,
                    )
                    stats["no_action"] += 1
                    continue

                # Analyze with Ollama (or fallback to rules if unavailable)
                if self._ollama_available is None:
                    self._ollama_available = await self.analyzer.health_check()
                    if not self._ollama_available:
                        console.print(
                            "[yellow]⚠ Ollama unavailable — using rule-based fallback[/yellow]\n"
                        )
                        logger.warning(
                            "LLM gateway unavailable — using rule-based fallback for all documents"
                        )

                extraction = None
                if self._ollama_available:
                    logger.info(
                        "doc_id=%s: entering LLM analysis step (model=%s)",
                        doc_id,
                        self.analyzer.model,
                    )
                    extraction = await self.analyzer.analyze_document(doc)
                    if not extraction:
                        # Ollama returned nothing — try fallback for this doc
                        logger.warning(
                            "doc_id=%s: LLM analysis failed — falling back to rule-based analyzer",
                            doc_id,
                        )
                        extraction = self.fallback_analyzer.analyze_document(doc)
                else:
                    extraction = self.fallback_analyzer.analyze_document(doc)

                if not extraction:
                    console.print(f"  [red]✗[/red] Analysis failed for document {doc_id}")
                    logger.error(
                        "doc_id=%s: analysis failed (LLM and fallback both returned no result)",
                        doc_id,
                    )
                    self._record_history(
                        db,
                        doc_id,
                        success=False,
                        error="Ollama returned no result",
                        disposition="low_confidence",
                        text_metrics=text_metrics,
                    )
                    stats["failed"] += 1
                    stats["errors"].append(
                        {
                            "document_id": doc_id,
                            "title": doc_title_short,
                            "error": "Analysis failed — LLM and fallback both returned no result",
                        }
                    )
                    continue

                assessment = extraction.get("document_assessment", {})
                actions = extraction.get("actions", [])
                overall_confidence = assessment.get("overall_confidence", 0)
                text_quality = assessment.get("text_quality", "fair")
                extracted_data = assessment.get("extracted_data")
                if not isinstance(extracted_data, dict):
                    extracted_data = {}
                account_identifier = pick_masked_account_identifier(
                    extract_account_numbers(content)
                )
                if account_identifier:
                    extracted_data["account_identifier"] = account_identifier
                extracted_data.pop("account_number", None)
                assessment["extracted_data"] = extracted_data

                # Handle unreadable text
                if text_quality == "unreadable":
                    console.print("  [yellow]⚠[/yellow] Text quality: unreadable")
                    logger.info("doc_id=%s: text quality unreadable", doc_id)
                    self._record_history(
                        db,
                        doc_id,
                        success=True,
                        disposition="unreadable",
                        text_metrics=text_metrics,
                    )
                    stats["no_action"] += 1
                    continue

                # Handle "no action needed" documents
                if not assessment.get("requires_action", True) or not actions:
                    console.print(
                        f"  [dim]  ○ No action needed[/dim] (confidence: {overall_confidence}%)"
                    )
                    logger.info(
                        "doc_id=%s: no action needed (confidence=%s%%)", doc_id, overall_confidence
                    )
                    sync_error: Exception | None = None
                    if settings.write_to_paperless:
                        if self._enrichment_available:
                            try:
                                await self.enricher.sync_status(doc_id, "not_an_action")
                            except Exception as exc:
                                sync_error = exc
                                logger.warning(
                                    "doc_id=%s: no-action resolution stored but Paperless sync failed: %s",
                                    doc_id,
                                    exc,
                                )
                        else:
                            sync_error = RuntimeError(
                                "Paperless custom-field enrichment is unavailable"
                            )
                        if sync_error is not None:
                            stats["enrichment_failed"] += 1
                    self._record_history(
                        db,
                        doc_id,
                        success=sync_error is None,
                        disposition=(
                            "no_action_needed" if sync_error is None else "no_action_sync_failed"
                        ),
                        error=str(sync_error) if sync_error is not None else None,
                        text_metrics=text_metrics,
                    )
                    stats["no_action"] += 1
                    continue

                # Store ALL actions in internal DB
                primary_idx = assessment.get("primary_action_index", 0)
                primary_action_data = (
                    actions[primary_idx] if primary_idx < len(actions) else actions[0]
                )
                existing_document_action = (
                    db.query(Action)
                    .filter(
                        Action.document_id == doc["id"],
                        Action.superseded_by_action_id.is_(None),
                    )
                    .order_by(Action.is_primary.desc(), Action.id.asc())
                    .first()
                )
                actions = sorted(
                    actions,
                    key=lambda item: (
                        str(item.get("action_type") or ""),
                        str(item.get("title") or ""),
                        str(item.get("due_date") or ""),
                        str(item.get("summary") or ""),
                    ),
                )
                (
                    db.query(Action)
                    .filter(
                        Action.document_id == doc["id"],
                        Action.parent_action_id.is_(None),
                        Action.superseded_by_action_id.is_(None),
                    )
                    .update({"is_primary": False}, synchronize_session=False)
                )
                stored_actions = []
                for i, action_data in enumerate(actions):
                    action = self._store_action(
                        db,
                        doc,
                        action_data,
                        assessment,
                        action_index=i,
                        is_primary=(action_data is primary_action_data),
                    )
                    stored_actions.append(action)
                document_amount_overridden = bool(
                    existing_document_action and existing_document_action.document_amount_overridden
                )
                document_due_date_overridden = bool(
                    existing_document_action
                    and existing_document_action.document_due_date_overridden
                )
                document_amount = (
                    existing_document_action.document_amount
                    if document_amount_overridden
                    else primary_action_data.get("amount")
                )
                document_due_date = (
                    existing_document_action.document_due_date
                    if document_due_date_overridden
                    else self._parse_date(primary_action_data.get("due_date"))
                )
                document_actions = (
                    db.query(Action)
                    .filter(
                        Action.document_id == doc["id"],
                        Action.superseded_by_action_id.is_(None),
                    )
                    .all()
                )
                for document_action in document_actions:
                    document_action.document_amount = document_amount
                    document_action.document_due_date = document_due_date
                    document_action.document_amount_overridden = document_amount_overridden
                    document_action.document_due_date_overridden = document_due_date_overridden
                db.flush()

                review_reasons: list[str] = []
                if overall_confidence < settings.confidence_threshold:
                    review_reasons.append(
                        f"Confidence {overall_confidence}% is below the configured "
                        f"{settings.confidence_threshold}% threshold"
                    )
                for stored_action in stored_actions:
                    if not action_has_critical_details(stored_action):
                        review_reasons.append(
                            f"{stored_action.action_type} is missing critical action details"
                        )
                if review_reasons:
                    reason = "; ".join(dict.fromkeys(review_reasons))
                    for stored_action in stored_actions:
                        route_action_to_review(db, stored_action, reason=reason)
                    if settings.write_to_paperless and self._enrichment_available:
                        primary_action = (
                            actions[primary_idx] if primary_idx < len(actions) else actions[0]
                        )
                        await self.enricher.enrich_document(
                            doc_id,
                            {**primary_action, **assessment},
                            action_status=None,
                            clear_action_inference=True,
                        )
                    self._record_history(
                        db,
                        doc_id,
                        success=True,
                        disposition="low_confidence",
                        error=reason,
                        text_metrics=text_metrics,
                    )
                    stats["no_action"] += 1
                    continue
                for stored_action in stored_actions:
                    mark_action_ready(stored_action)

                # Emit alerts inline for high-risk actions (best-effort)
                # Flush to ensure action IDs are assigned before emitting
                db.flush()
                self._emit_inline_alerts(stored_actions)

                # Enrich Paperless with PRIMARY action's data (only if writes enabled and available)
                primary_action = primary_action_data
                if settings.write_to_paperless and self._enrichment_available:
                    enrichment_data = {
                        **primary_action,
                        **assessment,
                        "amount": document_amount,
                        "document_due_date": (
                            document_due_date.isoformat() if document_due_date else None
                        ),
                    }
                    logger.info(
                        "doc_id=%s: enriching Paperless — action_type=%s urgency=%s fields=%s",
                        doc_id,
                        primary_action.get("action_type"),
                        primary_action.get("urgency"),
                        list(enrichment_data.keys()),
                    )
                    try:
                        await self.enricher.enrich_document(doc_id, enrichment_data)
                        logger.info("doc_id=%s: enrichment succeeded", doc_id)
                        # Track what we wrote so bidirectional sync knows our last state
                        for a in stored_actions:
                            a.last_synced_status = "pending"
                    except Exception as e:
                        console.print(f"  [yellow]⚠[/yellow] Stored but enrichment failed: {e}")
                        logger.warning(
                            "doc_id=%s: stored locally but enrichment to Paperless failed: %s",
                            doc_id,
                            e,
                        )
                        stats["enrichment_failed"] += 1

                action_summary = f"{primary_action['action_type']} — {primary_action['title'][:50]}"
                if len(actions) > 1:
                    action_summary += f" (+{len(actions) - 1} more)"
                console.print(
                    f"  [green]✓[/green] {action_summary} (confidence: {overall_confidence}%)"
                )
                logger.info("doc_id=%s: processed successfully — %s", doc_id, action_summary)

                self._record_history(
                    db,
                    doc_id,
                    success=True,
                    disposition="action_created",
                    text_metrics=text_metrics,
                )
                stats["processed"] += 1

            except Exception as e:
                # Isolate per-document failures so one bad document can't kill the whole run.
                console.print(f"  [red]✗[/red] Unexpected error processing document {doc_id}: {e}")
                logger.exception(
                    "doc_id=%s title=%r: unexpected error during processing — continuing to next document",
                    doc_id,
                    doc_title_short,
                )
                stats["failed"] += 1
                stats["errors"].append(
                    {
                        "document_id": doc_id,
                        "title": doc_title_short,
                        "error": str(e),
                    }
                )
                continue

        db.commit()
        db.close()

        total_duration = time.monotonic() - run_start
        _set_progress(
            current_step="complete",
            progress=f"{stats['processed']}/{total_docs} documents processed",
            current_document=None,
        )

        # Summary
        console.print("\n[bold green]Done![/bold green]")
        console.print(
            f"  Processed: {stats['processed']} | "
            f"No action: {stats['no_action']} | "
            f"Skipped: {stats['skipped']} | "
            f"Failed: {stats['failed']}"
        )
        logger.info(
            "Pipeline run complete: processed=%d no_action=%d skipped=%d failed=%d timed_out=%s duration=%.2fs",
            stats["processed"],
            stats["no_action"],
            stats["skipped"],
            stats["failed"],
            stats["timed_out"],
            total_duration,
        )
        stats["duration_seconds"] = round(total_duration, 2)
        return stats

    async def _fetch_documents(
        self,
        *,
        document_id: int | None,
        saved_view_id: int | None,
        tag_override: str | None,
        created_after: str | None,
        created_before: str | None,
        added_after: str | None,
        added_before: str | None,
        correspondent: str | None,
        document_type: str | None,
        fetch_limit: int | None,
    ) -> list[dict]:
        """Fetch the candidate document set for this run, applying server-side
        filtering (tags/dates/correspondent) and — when possible — a limit so
        we don't paginate through the entire Paperless collection.
        """
        if document_id:
            console.print(f"[dim]Fetching document #{document_id}...[/dim]")
            doc = await self.paperless.get_document(document_id)
            return [doc] if doc else []

        if saved_view_id:
            console.print(f"[dim]Fetching documents from saved view #{saved_view_id}...[/dim]")
            return await self.paperless.list_documents(saved_view=saved_view_id, limit=fetch_limit)

        if any(
            [created_after, created_before, added_after, added_before, correspondent, document_type]
        ):
            # Use flexible query with date/correspondent/type filters
            tags = [tag_override] if tag_override else None
            console.print("[dim]Fetching documents with custom filters...[/dim]")
            return await self.paperless.list_documents(
                tags=tags,
                created_after=created_after,
                created_before=created_before,
                added_after=added_after,
                added_before=added_before,
                correspondent=correspondent,
                document_type=document_type,
                limit=fetch_limit,
            )

        # Default: use configured intake tags.
        tags = [tag_override] if tag_override else settings.monitor_tags
        console.print(f"[dim]Fetching documents tagged: {tags}[/dim]")
        return await self.paperless.list_documents(tags=tags, limit=fetch_limit)

    def _store_action(
        self,
        db,
        document: dict,
        action_data: dict,
        assessment: dict,
        action_index: int = 0,
        is_primary: bool = True,
    ) -> Action:
        """Store or update an action in the internal database.

        Deduplication strategy:
        1. Exact match on document_id + title → update in place
        2. Match on document_id + action_type (pending only) → update existing
           (handles LLM producing slightly different titles on re-runs)
        3. No match → create new
        """
        doc_id = document["id"]

        # Resolve correspondent name from cache
        corr_raw = assessment.get("correspondent") or document.get("correspondent")
        if isinstance(corr_raw, int):
            correspondent_name = self._correspondent_cache.get(corr_raw, str(corr_raw))
        elif corr_raw and str(corr_raw).isdigit():
            correspondent_name = self._correspondent_cache.get(int(corr_raw), str(corr_raw))
        else:
            correspondent_name = str(corr_raw) if corr_raw else None

        # Resolve document type name from cache
        doc_type_raw = document.get("document_type")
        if isinstance(doc_type_raw, int):
            document_type_name = self._doc_type_cache.get(doc_type_raw, str(doc_type_raw))
        elif doc_type_raw and str(doc_type_raw).isdigit():
            document_type_name = self._doc_type_cache.get(int(doc_type_raw), str(doc_type_raw))
        else:
            document_type_name = str(doc_type_raw) if doc_type_raw else None

        # Prefer names supplied by Paperless, otherwise resolve its numeric tag IDs.
        raw_tags = document.get("tag_names")
        if not isinstance(raw_tags, list):
            raw_tags = document.get("tags", [])
        tag_names = [
            self._tag_cache.get(int(tag), str(tag))
            if isinstance(tag, int) or (isinstance(tag, str) and tag.isdigit())
            else str(tag)
            for tag in raw_tags
        ]

        # Extract document date (Paperless "created" field)
        document_date = self._parse_date(document.get("created"))

        # Stable analyzer position preserves one row per inferred action, even
        # when a document contains multiple actions of the same type.
        existing = (
            db.query(Action)
            .filter_by(document_id=doc_id, action_index=action_index)
            .filter(
                Action.parent_action_id.is_(None),
                Action.superseded_by_action_id.is_(None),
            )
            .first()
        )

        # Compatibility fallback for rows created before action ordinals existed.
        if not existing:
            existing = (
                db.query(Action)
                .filter_by(document_id=doc_id, title=action_data["title"])
                .filter(
                    Action.action_index.is_(None),
                    Action.parent_action_id.is_(None),
                    Action.superseded_by_action_id.is_(None),
                )
                .first()
            )

        # Re-evaluation may legitimately change both title and type. Reuse a
        # single pending review action so its deep link and audit trail survive.
        if not existing:
            review_candidates = (
                db.query(Action)
                .filter_by(document_id=doc_id, status="pending", review_state="needs_review")
                .all()
            )
            if len(review_candidates) == 1:
                existing = review_candidates[0]

        # Compute composite risk score
        risk = compute_risk_score(
            urgency=action_data["urgency"],
            due_date=self._parse_date(action_data.get("due_date")),
            amount=action_data.get("amount"),
            confidence=action_data.get("confidence", 0),
            action_type=action_data["action_type"],
        )

        if existing:
            existing.action_type = action_data["action_type"]
            existing.title = action_data["title"]
            existing.summary = action_data.get("summary")
            existing.due_date = self._parse_date(action_data.get("due_date"))
            existing.amount = action_data.get("amount")
            existing.urgency = action_data["urgency"]
            existing.severity = urgency_to_severity(action_data["urgency"])
            existing.confidence = action_data.get("confidence", 0)
            existing.risk_score = risk
            existing.correspondent = correspondent_name
            existing.document_date = document_date
            existing.document_type = document_type_name
            existing.tags = tag_names or None
            existing.extracted_data = assessment.get("extracted_data")
            existing.ai_reasoning = assessment.get("reasoning")
            existing.recommended_cta = _serialize_cta(action_data.get("recommended_cta"))
            existing.action_index = action_index
            existing.is_primary = is_primary
            existing.updated_at = datetime.utcnow()
            return existing
        else:
            action = Action(
                document_id=doc_id,
                document_title=document.get("title", f"Document #{doc_id}"),
                action_type=action_data["action_type"],
                title=action_data["title"],
                summary=action_data.get("summary"),
                due_date=self._parse_date(action_data.get("due_date")),
                amount=action_data.get("amount"),
                urgency=action_data["urgency"],
                severity=urgency_to_severity(action_data["urgency"]),
                confidence=action_data.get("confidence", 0),
                risk_score=risk,
                correspondent=correspondent_name,
                document_date=document_date,
                document_type=document_type_name,
                tags=tag_names or None,
                extracted_data=assessment.get("extracted_data"),
                ai_reasoning=assessment.get("reasoning"),
                recommended_cta=_serialize_cta(action_data.get("recommended_cta")),
                action_index=action_index,
                is_primary=is_primary,
                action_ready=True,
                review_state="ready",
            )
            db.add(action)
            return action

    def _resolve_document_metadata(self, document: dict) -> None:
        """Add resolved Paperless metadata names used by both analyzers."""
        correspondent = document.get("correspondent")
        if isinstance(correspondent, int):
            document["correspondent_name"] = self._correspondent_cache.get(
                correspondent, str(correspondent)
            )
        elif correspondent and str(correspondent).isdigit():
            document["correspondent_name"] = self._correspondent_cache.get(
                int(correspondent), str(correspondent)
            )

        document_type = document.get("document_type")
        if isinstance(document_type, int):
            document["document_type_name"] = self._doc_type_cache.get(
                document_type, str(document_type)
            )
        elif document_type and str(document_type).isdigit():
            document["document_type_name"] = self._doc_type_cache.get(
                int(document_type), str(document_type)
            )
        elif document_type:
            document["document_type_name"] = str(document_type)

        resolved_tags = []
        for tag in document.get("tag_names", document.get("tags", [])):
            if isinstance(tag, int):
                resolved_tags.append(self._tag_cache.get(tag, str(tag)))
            elif str(tag).isdigit():
                resolved_tags.append(self._tag_cache.get(int(tag), str(tag)))
            else:
                resolved_tags.append(str(tag))
        document["tag_names"] = resolved_tags

    def _record_history(
        self,
        db,
        document_id: int,
        success: bool,
        disposition: str = "action_created",
        error: str | None = None,
        text_metrics: dict[str, Any] | None = None,
    ):
        """Record processing attempt in history table."""
        metrics = text_metrics or {}
        existing = db.query(ProcessingHistory).filter_by(document_id=document_id).first()
        if existing:
            existing.processed_at = datetime.utcnow()
            existing.ollama_model = settings.ollama_model
            existing.success = 1 if success else 0
            existing.error_message = error
            existing.disposition = disposition
            existing.content_length = metrics.get("content_length")
            existing.word_count = metrics.get("word_count")
            existing.text_quality_score = metrics.get("text_quality_score")
        else:
            record = ProcessingHistory(
                document_id=document_id,
                ollama_model=settings.ollama_model,
                success=1 if success else 0,
                error_message=error,
                disposition=disposition,
                content_length=metrics.get("content_length"),
                word_count=metrics.get("word_count"),
                text_quality_score=metrics.get("text_quality_score"),
            )
            db.add(record)

    @staticmethod
    def _compute_text_quality(content: str) -> dict:
        """Compute basic text quality heuristics (free data for OCR pipeline)."""
        if not content:
            return {"content_length": 0, "word_count": 0, "text_quality_score": 0}

        words = content.split()
        word_count = len(words)
        content_length = len(content)

        # Simple heuristic score (0-100):
        # - Penalize very short content
        # - Penalize high ratio of non-alpha characters (garbled OCR)
        # - Penalize very short average word length (broken words)
        score = 100

        if content_length < 50:
            score -= 40
        elif content_length < 200:
            score -= 20

        if word_count > 0:
            avg_word_len = content_length / word_count
            if avg_word_len < 3:
                score -= 30  # Words too short (broken OCR)
            elif avg_word_len > 15:
                score -= 20  # Words too long (no spaces detected)

            # Ratio of alphabetic characters
            alpha_chars = sum(1 for c in content if c.isalpha())
            alpha_ratio = alpha_chars / content_length if content_length > 0 else 0
            if alpha_ratio < 0.4:
                score -= 25  # Too much noise

        return {
            "content_length": content_length,
            "word_count": word_count,
            "text_quality_score": max(0, min(100, score)),
        }

    @staticmethod
    def _emit_inline_alerts(stored_actions: list[Action]) -> None:
        """Emit alerts for high-risk actions created during pipeline run.

        Only emits for CRITICAL/HIGH urgency or high risk_score actions to
        avoid flooding the alert system during bulk processing.
        """
        try:
            from doc_intelligence_hub.core.alerts import emit_alert

            for action in stored_actions:
                urgency = (action.urgency or "LOW").upper()
                if urgency == "CRITICAL" or action.risk_score >= 70:
                    emit_alert(
                        alert_type="high_risk_action_created",
                        severity="high" if urgency == "CRITICAL" else "medium",
                        module="action_queue",
                        title=f"High-risk action: {action.title[:80]}",
                        description=(
                            f"Action created with risk score {action.risk_score}/100 "
                            f"(urgency: {urgency}, type: {action.action_type})."
                        ),
                        metadata={
                            "action_id": action.id,
                            "document_id": action.document_id,
                            "risk_score": action.risk_score,
                            "urgency": urgency,
                            "action_type": action.action_type,
                            "due_date": action.due_date.isoformat() if action.due_date else None,
                            "amount": action.amount,
                        },
                    )
        except Exception:
            logger.debug(
                "Alert emission skipped (best-effort): circuit may be open or alert system unavailable"
            )

    @staticmethod
    def _parse_date(date_str: str | None) -> date | None:
        """Parse a date string, returning None on failure."""
        if not date_str:
            return None
        try:
            from dateutil.parser import parse

            return parse(date_str).date()
        except (ValueError, TypeError):
            return None


async def run_pipeline(
    force: bool = False,
    limit: int | None = None,
    document_id: int | None = None,
    tag_override: str | None = None,
    saved_view_id: int | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    added_after: str | None = None,
    added_before: str | None = None,
    correspondent: str | None = None,
    document_type: str | None = None,
    dry_run: bool = False,
    min_start_interval_seconds: float = 0,
) -> dict:
    """Entry point for serialized scheduled, manual, and fast-path runs."""
    global _last_pipeline_start

    async with _pipeline_run_lock:
        if min_start_interval_seconds > 0 and _last_pipeline_start is not None:
            elapsed = time.monotonic() - _last_pipeline_start
            delay = min_start_interval_seconds - elapsed
            if delay > 0:
                await asyncio.sleep(delay)
        _last_pipeline_start = time.monotonic()

        pipeline = Pipeline()
        return await pipeline.run(
            force=force,
            limit=limit,
            document_id=document_id,
            tag_override=tag_override,
            saved_view_id=saved_view_id,
            created_after=created_after,
            created_before=created_before,
            added_after=added_after,
            added_before=added_before,
            correspondent=correspondent,
            document_type=document_type,
            dry_run=dry_run,
        )
