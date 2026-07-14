"""Main processing pipeline — orchestrates fetch → analyze → store → enrich."""

import asyncio
import io
from datetime import datetime, date
from typing import Optional

from rich.console import Console
from rich.table import Table

from .config import settings
from .database import get_session, init_db, Action, ProcessingHistory
from doc_intelligence_hub.core.paperless import PaperlessClient
from .analyzer import OllamaAnalyzer
from .fallback_analyzer import RuleBasedAnalyzer
from .enricher import PaperlessEnricher

# Use a file-based console to avoid Windows encoding issues when running under uvicorn
console = Console(file=io.StringIO(), force_terminal=False, highlight=False)


def _make_paperless_client() -> PaperlessClient:
    """Create a PaperlessClient from action queue settings."""
    return PaperlessClient(base_url=settings.paperless_url, token=settings.paperless_token)


class Pipeline:
    """Orchestrates the document analysis pipeline."""

    def __init__(self):
        self.paperless = _make_paperless_client()
        self.analyzer = OllamaAnalyzer()
        self.fallback_analyzer = RuleBasedAnalyzer()
        self.enricher = PaperlessEnricher()
        self._ollama_available: Optional[bool] = None

    async def run(
        self,
        force: bool = False,
        limit: Optional[int] = None,
        document_id: Optional[int] = None,
        tag_override: Optional[str] = None,
        saved_view_id: Optional[int] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        added_after: Optional[str] = None,
        added_before: Optional[str] = None,
        correspondent: Optional[str] = None,
        document_type: Optional[str] = None,
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

        # Build a correspondent ID→name cache
        self._correspondent_cache: dict[int, str] = {}
        try:
            correspondents = await self.paperless.list_correspondents()
            self._correspondent_cache = {c["id"]: c["name"] for c in correspondents}
        except Exception:
            pass  # Non-fatal — we'll fall back to IDs

        # Step 1: Ensure custom fields exist (skip in dry-run or read-only mode)
        if not dry_run and settings.write_to_paperless:
            console.print("[dim]Checking custom fields...[/dim]")
            await self.enricher.ensure_custom_fields_exist()
            console.print("[green]✓[/green] Custom fields ready\n")

        # Step 2: Fetch documents based on provided filters
        if document_id:
            console.print(f"[dim]Fetching document #{document_id}...[/dim]")
            doc = await self.paperless.get_document(document_id)
            documents = [doc] if doc else []
        elif saved_view_id:
            console.print(f"[dim]Fetching documents from saved view #{saved_view_id}...[/dim]")
            documents = await self.paperless.list_documents(saved_view=saved_view_id)
        elif any([created_after, created_before, added_after, added_before, correspondent, document_type]):
            # Use flexible query with date/correspondent/type filters
            tags = [tag_override] if tag_override else None
            console.print("[dim]Fetching documents with custom filters...[/dim]")
            documents = await self.paperless.list_documents(
                tags=tags,
                created_after=created_after,
                created_before=created_before,
                added_after=added_after,
                added_before=added_before,
                correspondent=correspondent,
                document_type=document_type,
            )
        else:
            # Default: use configured tags (Inbox, Todo)
            tags = [tag_override] if tag_override else settings.monitor_tags
            console.print(f"[dim]Fetching documents tagged: {tags}[/dim]")
            documents = await self.paperless.list_documents(tags=tags)

        console.print(f"[green]✓[/green] Found {len(documents)} documents")

        console.print()

        if not documents:
            console.print("[yellow]No documents to process.[/yellow]")
            return {"processed": 0, "skipped": 0, "failed": 0}

        # Filter out already-processed documents (unless force re-scan)
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

        # Apply limit AFTER filtering (limit means "analyze up to N new docs")
        if limit and len(documents) > limit:
            documents = documents[:limit]
            console.print(f"[dim]  (limited to {limit})[/dim]")

        if not documents:
            console.print("[yellow]No new documents to process.[/yellow]")
            return {"processed": 0, "skipped": skipped_count if not force else 0, "failed": 0, "no_action": 0}

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
            return {"processed": 0, "skipped": 0, "failed": 0, "would_process": len(documents)}

        # Step 3: Process each document
        init_db()
        db = get_session()
        stats = {"processed": 0, "skipped": 0, "failed": 0, "no_action": 0}

        for doc in documents:
            doc_id = doc["id"]
            doc_title = doc.get("title", f"Document #{doc_id}")

            console.print(f"  [dim]Analyzing:[/dim] {doc_title[:60]}...")

            # Fetch full content if not already present
            if "content" not in doc or not doc["content"]:
                doc["content"] = await self.paperless.get_document_content(doc_id)

            # Compute basic text quality metrics
            content = doc.get("content", "")
            text_metrics = self._compute_text_quality(content)

            # Check if content is too short/empty to analyze
            if text_metrics["content_length"] < 20:
                console.print(f"  [yellow]⚠[/yellow] No/minimal text content — marking unreadable")
                self._record_history(
                    db, doc_id, success=True, disposition="unreadable",
                    text_metrics=text_metrics,
                )
                stats["no_action"] += 1
                continue

            # Analyze with Ollama (or fallback to rules if unavailable)
            if self._ollama_available is None:
                self._ollama_available = await self.analyzer.health_check()
                if not self._ollama_available:
                    console.print("[yellow]⚠ Ollama unavailable — using rule-based fallback[/yellow]\n")

            extraction = None
            if self._ollama_available:
                extraction = await self.analyzer.analyze_document(doc)
                if not extraction:
                    # Ollama returned nothing — try fallback for this doc
                    extraction = self.fallback_analyzer.analyze_document(doc)
            else:
                extraction = self.fallback_analyzer.analyze_document(doc)

            if not extraction:
                console.print(f"  [red]✗[/red] Analysis failed for document {doc_id}")
                self._record_history(
                    db, doc_id, success=False, error="Ollama returned no result",
                    disposition="low_confidence", text_metrics=text_metrics,
                )
                stats["failed"] += 1
                continue

            assessment = extraction.get("document_assessment", {})
            actions = extraction.get("actions", [])
            overall_confidence = assessment.get("overall_confidence", 0)
            text_quality = assessment.get("text_quality", "fair")

            # Handle unreadable text
            if text_quality == "unreadable":
                console.print(f"  [yellow]⚠[/yellow] Text quality: unreadable")
                self._record_history(
                    db, doc_id, success=True, disposition="unreadable",
                    text_metrics=text_metrics,
                )
                stats["no_action"] += 1
                continue

            # Handle "no action needed" documents
            if not assessment.get("requires_action", True) or not actions:
                console.print(f"  [dim]  ○ No action needed[/dim] (confidence: {overall_confidence}%)")
                self._record_history(
                    db, doc_id, success=True, disposition="no_action_needed",
                    text_metrics=text_metrics,
                )
                stats["no_action"] += 1
                continue

            # Check confidence threshold
            if overall_confidence < settings.confidence_threshold:
                console.print(
                    f"  [yellow]⚠[/yellow] Low confidence ({overall_confidence}%) — recording but not enriching"
                )
                self._record_history(
                    db, doc_id, success=True, disposition="low_confidence",
                    error=f"Below threshold: {overall_confidence}%",
                    text_metrics=text_metrics,
                )
                stats["no_action"] += 1
                continue

            # Store ALL actions in internal DB
            primary_idx = assessment.get("primary_action_index", 0)
            stored_actions = []
            for i, action_data in enumerate(actions):
                action = self._store_action(
                    db, doc, action_data, assessment,
                    is_primary=(i == primary_idx),
                )
                stored_actions.append(action)

            # Enrich Paperless with PRIMARY action's data (only if writes enabled)
            primary_action = actions[primary_idx] if primary_idx < len(actions) else actions[0]
            if settings.write_to_paperless:
                enrichment_data = {**primary_action, **assessment}
                try:
                    await self.enricher.enrich_document(doc_id, enrichment_data, action_count=len(actions))
                    # Track what we wrote so bidirectional sync knows our last state
                    for a in stored_actions:
                        a.last_synced_status = "pending"
                except Exception as e:
                    console.print(f"  [yellow]⚠[/yellow] Stored but enrichment failed: {e}")

            action_summary = f"{primary_action['action_type']} — {primary_action['title'][:50]}"
            if len(actions) > 1:
                action_summary += f" (+{len(actions)-1} more)"
            console.print(f"  [green]✓[/green] {action_summary} (confidence: {overall_confidence}%)")

            self._record_history(
                db, doc_id, success=True, disposition="action_created",
                text_metrics=text_metrics,
            )
            stats["processed"] += 1

        db.commit()
        db.close()

        # Summary
        console.print(f"\n[bold green]Done![/bold green]")
        console.print(
            f"  Processed: {stats['processed']} | "
            f"No action: {stats['no_action']} | "
            f"Skipped: {stats['skipped']} | "
            f"Failed: {stats['failed']}"
        )
        return stats

    def _store_action(self, db, document: dict, action_data: dict, assessment: dict, is_primary: bool = True) -> Action:
        """Store or update an action in the internal database."""
        doc_id = document["id"]

        # Resolve correspondent name from cache
        corr_raw = assessment.get("correspondent") or document.get("correspondent")
        if isinstance(corr_raw, int):
            correspondent_name = self._correspondent_cache.get(corr_raw, str(corr_raw))
        elif corr_raw and str(corr_raw).isdigit():
            correspondent_name = self._correspondent_cache.get(int(corr_raw), str(corr_raw))
        else:
            correspondent_name = str(corr_raw) if corr_raw else None

        # For multi-action docs, check by document_id + title to avoid clobbering
        existing = (
            db.query(Action)
            .filter_by(document_id=doc_id, title=action_data["title"])
            .first()
        )

        if existing:
            existing.action_type = action_data["action_type"]
            existing.title = action_data["title"]
            existing.summary = action_data.get("summary")
            existing.due_date = self._parse_date(action_data.get("due_date"))
            existing.amount = action_data.get("amount")
            existing.urgency = action_data["urgency"]
            existing.confidence = action_data.get("confidence", 0)
            existing.correspondent = correspondent_name
            existing.extracted_data = assessment.get("extracted_data")
            existing.ai_reasoning = assessment.get("reasoning")
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
                confidence=action_data.get("confidence", 0),
                correspondent=correspondent_name,
                extracted_data=assessment.get("extracted_data"),
                ai_reasoning=assessment.get("reasoning"),
            )
            db.add(action)
            return action

    def _record_history(self, db, document_id: int, success: bool,
                        disposition: str = "action_created",
                        error: str = None,
                        text_metrics: dict = None):
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
    def _parse_date(date_str: Optional[str]) -> Optional[date]:
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
    limit: Optional[int] = None,
    document_id: Optional[int] = None,
    tag_override: Optional[str] = None,
    saved_view_id: Optional[int] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    added_after: Optional[str] = None,
    added_before: Optional[str] = None,
    correspondent: Optional[str] = None,
    document_type: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Entry point for running the pipeline."""
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
