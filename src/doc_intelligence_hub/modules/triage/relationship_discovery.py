"""Paperless-backed discovery for historical relationship scans."""

from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import date
from typing import Any, Iterable

PAPERLESS_PAGE_SIZE = 200
MAX_EXCLUSION_REASONS = 10
MAX_CANDIDATES_PER_DOCUMENT = 8

_FIELD_ALIASES = {
    "provider": ("provider", "provider name", "payee"),
    "invoice_number": ("invoice number", "invoice", "bill number"),
    "claim_number": ("claim number", "claim"),
    "account_identifier": ("account identifier", "account number", "account"),
    "amount": ("amount", "total amount", "balance", "amount due"),
    "date_of_service": ("date of service", "service date"),
    "document_date": ("document date", "notice date"),
}


def _normalized(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _field_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _first_value(values: dict[str, Any], aliases: Iterable[str]) -> Any:
    for alias in aliases:
        value = values.get(alias)
        if value not in (None, ""):
            return value
    return None


def _custom_field_values(document: dict[str, Any], field_names: dict[int, str]) -> dict[str, Any]:
    raw = document.get("custom_fields")
    if isinstance(raw, dict):
        return {_field_name(key): value for key, value in raw.items()}
    values: dict[str, Any] = {}
    if not isinstance(raw, list):
        return values
    for item in raw:
        if not isinstance(item, dict):
            continue
        field_id = item.get("field")
        name = item.get("name") or field_names.get(field_id)
        if name:
            values[_field_name(name)] = item.get("value")
    return values


def normalize_paperless_document(
    document: dict[str, Any],
    *,
    correspondents: dict[int, str],
    field_names: dict[int, str],
) -> dict[str, Any] | None:
    """Return only the structured metadata used by relationship classification."""
    document_id = document.get("id")
    if isinstance(document_id, bool) or not isinstance(document_id, int):
        return None
    custom = _custom_field_values(document, field_names)
    correspondent = document.get("correspondent")
    provider = (
        correspondents.get(correspondent) if isinstance(correspondent, int) else correspondent
    ) or _first_value(custom, _FIELD_ALIASES["provider"])
    normalized = {
        "document_id": document_id,
        "title": document.get("title"),
        "provider": provider,
        "correspondent": provider,
        "invoice_number": _first_value(custom, _FIELD_ALIASES["invoice_number"]),
        "claim_number": _first_value(custom, _FIELD_ALIASES["claim_number"]),
        "account_identifier": _first_value(custom, _FIELD_ALIASES["account_identifier"]),
        "amount": _first_value(custom, _FIELD_ALIASES["amount"]),
        "date_of_service": _first_value(custom, _FIELD_ALIASES["date_of_service"]),
        "document_date": (
            _first_value(custom, _FIELD_ALIASES["document_date"])
            or document.get("created_date")
            or document.get("created")
        ),
    }
    return normalized


async def discover_paperless_documents(
    client: Any,
    *,
    scope: dict[str, Any],
    action_queue_source: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch one configured Paperless scope using the shared bounded paginator."""
    kind = scope["kind"]
    query: dict[str, Any] = {
        "page_size": PAPERLESS_PAGE_SIZE,
        "on_progress": scope.get("on_progress"),
    }
    description = ""
    if kind == "action_queue":
        if action_queue_source.get("scan_mode") == "saved_view" and action_queue_source.get(
            "saved_view_id"
        ):
            query["saved_view"] = action_queue_source["saved_view_id"]
            description = f"Action Queue saved view #{action_queue_source['saved_view_id']}"
        else:
            tags = list(action_queue_source.get("monitor_tags") or [])
            if not tags:
                raise ValueError("The configured Action Queue source has no monitored tags")
            query["tags"] = tags
            description = f"Action Queue tags: {', '.join(tags)}"
        query["limit"] = action_queue_source.get("document_limit")
    elif kind == "tag":
        query["tags"] = scope["tags"]
        description = f"Paperless tags: {', '.join(scope['tags'])}"
    elif kind == "saved_view":
        query["saved_view"] = scope["saved_view_id"]
        description = f"Paperless saved view #{scope['saved_view_id']}"
    elif kind == "date_range":
        query["created_after"] = scope.get("created_after")
        query["created_before"] = scope.get("created_before")
        description = (
            f"Paperless document dates {scope.get('created_after') or 'any'}"
            f" through {scope.get('created_before') or 'any'}"
        )
    elif kind == "all":
        description = "All Paperless documents"
    else:
        raise ValueError("Unsupported relationship scan scope")

    documents = await client.list_documents(
        **{key: value for key, value in query.items() if value is not None}
    )
    correspondents, _, _ = await client.fetch_all_metadata()
    fields = await client.list_custom_fields()
    field_names = {
        item["id"]: item["name"]
        for item in fields
        if isinstance(item, dict)
        and isinstance(item.get("id"), int)
        and isinstance(item.get("name"), str)
    }
    normalized = [
        item
        for document in documents
        if (
            item := normalize_paperless_document(
                document,
                correspondents=correspondents,
                field_names=field_names,
            )
        )
        is not None
    ]
    by_id = {document["document_id"]: document for document in normalized}
    candidate_pairs, _, _ = build_relationship_candidate_pairs(by_id)
    candidate_ids = sorted({document_id for pair in candidate_pairs for document_id in pair})
    semaphore = asyncio.Semaphore(8)

    async def fetch_candidate_content(document_id: int) -> tuple[int, str | None]:
        async with semaphore:
            try:
                content = await client.get_document_content(document_id)
            except Exception:
                return document_id, None
            return document_id, content if isinstance(content, str) else None

    if candidate_ids:
        results = await asyncio.gather(
            *(fetch_candidate_content(document_id) for document_id in candidate_ids)
        )
        for document_id, content in results:
            if content:
                by_id[document_id]["content"] = content
    return normalized, {
        "scope": {"kind": kind, "description": description},
        "documents_discovered": len(documents),
    }


def _amount(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _amount_close(left: float, right: float) -> bool:
    return abs(left - right) <= max(1.0, max(abs(left), abs(right)) * 0.05)


def _amount_pairs(documents: list[tuple[int, float]]) -> Iterable[tuple[int, int]]:
    ordered = sorted(documents, key=lambda item: (item[1], item[0]))
    for index, (left_id, left_amount) in enumerate(ordered):
        matches = 0
        for right_id, right_amount in ordered[index + 1 :]:
            if not _amount_close(left_amount, right_amount):
                if right_amount - left_amount > max(
                    1.0, max(abs(left_amount), abs(right_amount)) * 0.05
                ):
                    break
                continue
            yield min(left_id, right_id), max(left_id, right_id)
            matches += 1
            if matches >= MAX_CANDIDATES_PER_DOCUMENT:
                break


def _bounded_exact_pairs(
    document_ids: list[int], documents: dict[int, dict[str, Any]]
) -> set[tuple[int, int]]:
    ordered = sorted(
        set(document_ids),
        key=lambda document_id: (
            str(documents[document_id].get("document_date") or ""),
            document_id,
        ),
    )
    return {
        (min(left, right), max(left, right))
        for index, left in enumerate(ordered)
        for right in ordered[index + 1 : index + 1 + MAX_CANDIDATES_PER_DOCUMENT]
    }


def build_relationship_candidate_pairs(
    documents: dict[int, dict[str, Any]],
) -> tuple[list[tuple[int, int]], int, dict[str, int]]:
    """Build deterministic compatible buckets without an all-pairs comparison."""
    exact: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    amount_groups: dict[tuple[str, str, str], list[tuple[int, float]]] = defaultdict(list)
    exclusions: Counter[str] = Counter()

    for document_id, metadata in sorted(documents.items()):
        provider = _normalized(
            metadata.get("provider")
            or metadata.get("provider_name")
            or metadata.get("correspondent")
        )
        if not provider:
            exclusions["missing_provider"] += 1
            continue
        indexed = False
        for field in ("invoice_number", "claim_number"):
            identity = _normalized(metadata.get(field))
            if identity:
                exact[(provider, field, identity)].append(document_id)
                indexed = True
        amount = _amount(metadata.get("amount", metadata.get("total_amount")))
        if amount is not None:
            account = _normalized(
                metadata.get("account_identifier") or metadata.get("account_hint")
            )
            if account:
                amount_groups[(provider, "account", account)].append((document_id, amount))
                indexed = True
            service_date = str(metadata.get("date_of_service") or "").strip()
            if service_date:
                amount_groups[(provider, "service_date", service_date)].append(
                    (document_id, amount)
                )
                indexed = True
        if not indexed:
            exclusions["missing_obligation_identity"] += 1

    pairs: set[tuple[int, int]] = set()
    candidate_groups = 0
    for document_ids in exact.values():
        group_pairs = _bounded_exact_pairs(document_ids, documents)
        if group_pairs:
            candidate_groups += 1
            pairs.update(group_pairs)
    for grouped_documents in amount_groups.values():
        group_pairs = set(_amount_pairs(grouped_documents))
        if group_pairs:
            candidate_groups += 1
            pairs.update(group_pairs)
    return (
        sorted(pairs),
        candidate_groups,
        dict(exclusions.most_common(MAX_EXCLUSION_REASONS)),
    )


def validate_date_range(created_after: date | None, created_before: date | None) -> None:
    if created_after and created_before and created_after > created_before:
        raise ValueError("The start date must be on or before the end date")
