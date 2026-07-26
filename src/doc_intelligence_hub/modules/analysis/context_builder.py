"""Context Builder — assembles data needed for rule evaluation.

Fetches document data from Paperless, series history from the statement
tracker DB, and related EOB matches from the EOB matching DB.
"""

from __future__ import annotations

import logging
from typing import Any

from doc_intelligence_hub.modules.analysis.models import ContextData, RuleConfig

logger = logging.getLogger(__name__)


async def build_context(rule: RuleConfig, *, document_id: int | None = None) -> ContextData:
    """Assemble the data a rule needs for evaluation.

    Args:
        rule: The rule configuration (specifies what context is needed).
        document_id: Optional Paperless document ID for document_added triggers.

    Returns:
        ContextData with all available fields populated.
    """
    ctx = ContextData()

    context_reqs = rule.context or []

    # Fetch current document if needed
    if document_id and ("current_document" in context_reqs or not context_reqs):
        ctx.current_document = await _fetch_document(document_id)

    # Fetch series history if needed
    series_depth = _get_series_depth(context_reqs)
    if series_depth and ctx.current_document:
        series_id = _extract_series_id(ctx.current_document)
        if series_id:
            ctx.series_info = await _fetch_series_info(series_id)
            ctx.series_history = await _fetch_series_history(series_id, limit=series_depth)

    # Fetch extracted fields if specified
    field_reqs = _get_field_requirements(context_reqs)
    if field_reqs and ctx.current_document:
        ctx.extracted_fields = _extract_fields(ctx.current_document, field_reqs)

    # Fetch related EOB matches if needed
    if "related_matches" in context_reqs and document_id:
        ctx.related_matches = await _fetch_related_matches(document_id)

    # Pass through rule params as extra context
    ctx.extra = dict(rule.params)

    return ctx


async def build_batch_context(rule: RuleConfig) -> list[ContextData]:
    """Build context for batch/scheduled rules that operate on multiple documents.

    Returns a list of ContextData, one per document/series that the rule should evaluate.
    """
    contexts: list[ContextData] = []

    trigger_filter = rule.trigger.filter
    doc_type = None
    if trigger_filter:
        doc_type = trigger_filter.document_type
        if isinstance(doc_type, list):
            doc_type = doc_type[0] if doc_type else None

    # For scheduled rules, fetch all relevant series
    series_list = await _fetch_all_series(doc_type_filter=doc_type)

    for series in series_list:
        series_id = series.get("id") or series.get("series_id")
        if not series_id:
            continue

        ctx = ContextData(
            series_info=series,
            series_history=await _fetch_series_history(series_id, limit=12),
            extra=dict(rule.params),
        )

        # Get the most recent document in the series as "current"
        if ctx.series_history:
            ctx.current_document = ctx.series_history[0]
            ctx.extracted_fields = _extract_fields(ctx.current_document, [])

        contexts.append(ctx)

    return contexts


# ------------------------------------------------------------------
# Internal data fetchers
# ------------------------------------------------------------------


async def _fetch_document(document_id: int) -> dict[str, Any] | None:
    """Fetch a document from Paperless via the hub's client."""
    try:
        from doc_intelligence_hub.core.paperless.client import get_client

        client = get_client()
        if client:
            doc = await client.get_document(document_id)
            return doc
    except Exception as exc:
        logger.warning("Could not fetch document %d: %s", document_id, exc)
    return None


async def _fetch_series_info(series_id: str) -> dict[str, Any] | None:
    """Fetch series metadata from the statement tracker."""
    try:
        from doc_intelligence_hub.modules.statements.database import Database

        db = Database()
        catalog = db.load_catalog()
        for provider in catalog.get("providers", {}).values():
            for series in provider.get("series", {}).values():
                if series.get("id") == series_id or series.get("series_id") == series_id:
                    return series
    except Exception as exc:
        logger.debug("Could not fetch series info for %s: %s", series_id, exc)
    return None


async def _fetch_series_history(series_id: str, limit: int = 6) -> list[dict[str, Any]]:
    """Fetch recent documents in a series from statement tracker."""
    try:
        from doc_intelligence_hub.modules.statements.database import Database

        db = Database()
        entries = db.load_series_entries(series_id, limit=limit)
        return entries if isinstance(entries, list) else []
    except Exception as exc:
        logger.debug("Could not fetch series history for %s: %s", series_id, exc)
    return []


async def _fetch_all_series(doc_type_filter: str | None = None) -> list[dict[str, Any]]:
    """Fetch all known statement series."""
    try:
        from doc_intelligence_hub.modules.statements.database import Database

        db = Database()
        catalog = db.load_catalog()
        all_series = []
        for provider in catalog.get("providers", {}).values():
            for series in provider.get("series", {}).values():
                if doc_type_filter:
                    s_type = series.get("document_type", "")
                    if s_type and s_type != doc_type_filter:
                        continue
                all_series.append(series)
        return all_series
    except Exception as exc:
        logger.debug("Could not fetch series catalog: %s", exc)
    return []


async def _fetch_related_matches(document_id: int) -> list[dict[str, Any]]:
    """Fetch EOB matches related to a document."""
    try:
        from doc_intelligence_hub.modules.eob_matching.database import get_matches_for_document

        return get_matches_for_document(document_id) or []
    except Exception as exc:
        logger.debug("Could not fetch matches for doc %d: %s", document_id, exc)
    return []


def _extract_series_id(document: dict[str, Any]) -> str | None:
    """Extract series ID from a document's metadata."""
    # Check custom fields or tags
    for key in ("series_id", "statement_series_id", "series"):
        val = document.get(key)
        if val:
            return str(val)

    # Check nested custom_fields
    custom = document.get("custom_fields", {})
    if isinstance(custom, dict):
        for key in ("series_id", "statement_series_id"):
            if key in custom:
                return str(custom[key])

    return None


def _get_series_depth(context_reqs: list[str]) -> int | None:
    """Extract series history depth from context requirements like 'series_history: 6'."""
    for req in context_reqs:
        if isinstance(req, str) and req.startswith("series_history"):
            # Handle both "series_history" and "series_history: 6"
            parts = req.split(":")
            if len(parts) > 1:
                try:
                    return int(parts[1].strip())
                except ValueError:
                    pass
            return 6  # default
        if isinstance(req, dict) and "series_history" in req:
            return int(req["series_history"])
    return None


def _get_field_requirements(context_reqs: list[str]) -> list[str]:
    """Extract field names from context requirements."""
    for req in context_reqs:
        if isinstance(req, dict) and "extracted_fields" in req:
            return req["extracted_fields"]
    return []


def _extract_fields(document: dict[str, Any], field_names: list[str]) -> dict[str, Any]:
    """Extract specific fields from a document."""
    if not field_names:
        # Return all known numeric/important fields
        result = {}
        for key in ("total_amount", "closing_balance", "statement_period", "due_date", "amount_due"):
            if key in document:
                result[key] = document[key]
        custom = document.get("custom_fields", {})
        if isinstance(custom, dict):
            result.update(custom)
        return result

    result = {}
    for name in field_names:
        if name in document:
            result[name] = document[name]
        elif isinstance(document.get("custom_fields"), dict) and name in document["custom_fields"]:
            result[name] = document["custom_fields"][name]
    return result
